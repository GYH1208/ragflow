import time
import httpx
import pytest

ISSUER = "https://oauth2.account.tencent.com/authz/oidc/v2/123"


def make_client(overrides=None, user_overrides=None, dept_overrides=None, redirect=False):
    from api.auth.workbuddy.config import WorkBuddyConfig
    from api.auth.workbuddy.oneid_client import OneIDClient

    calls = []
    info = dict(active=True, client_id="codebuddy", aud=["i-test"], iss=ISSUER, tid="123", sub="employee-1", exp=int(time.time()) + 300)
    info.update(overrides or {})
    user = dict(union_id="employee-1", name="员工", alias_id="worker", status="Enabled", departments=[dict(department_id="3", is_main_department=True)])
    user.update(user_overrides or {})

    async def handler(req):
        calls.append(req)
        path = req.url.path
        if redirect:
            return httpx.Response(302, headers={"Location": "https://attacker.invalid/"})
        if path.endswith("openid-configuration"):
            data = dict(issuer=ISSUER, introspection_endpoint=ISSUER + "/introspect", userinfo_endpoint=ISSUER + "/userinfo", introspection_endpoint_auth_methods_supported=["client_secret_post"])
        elif path.endswith("/introspect"):
            data = info
        elif path.endswith("/userinfo"):
            data = dict(sub="employee-1", tid="123")
        elif path.endswith("/token"):
            data = dict(access_token="application-secret-token", expires_in=600)
        elif "/users/" in path:
            data = dict(code=0, data=dict(user=user))
        elif path.endswith("/roots"):
            data = dict(code=0, data=dict(roots=[dict(department_id="1")]))
        else:
            dept = path.rsplit("/", 1)[1]
            node = dict(department_id=dept, department_name="同名部门", **({"department_parent_id": str(int(dept) - 1)} if dept != "1" else {}))
            node.update((dept_overrides or {}).get(dept, {}))
            data = dict(code=0, data=dict(department=node))
        return httpx.Response(200, json=data)

    conf = WorkBuddyConfig(True, ISSUER, "123", "i-test", "server-secret", frozenset({"codebuddy"}))
    return OneIDClient(conf, transport=httpx.MockTransport(handler)), calls


async def test_requester_and_audience_are_distinct():
    client, calls = make_client()
    async with client:
        employee = await client.verify_employee("personal-ticket")
        assert employee.union_id == "employee-1"
        assert employee.paths[0].complete is True
        assert [n.id for n in employee.paths[0].hierarchy] == ["1", "2", "3"]
        await client.verify_employee("personal-ticket")
    assert sum(r.url.path.endswith("/introspect") for r in calls) == 2
    assert sum("/users/" in r.url.path for r in calls) == 2
    assert sum(r.url.path.endswith("/token") for r in calls) == 1
    assert "personal-ticket" not in repr(employee)


@pytest.mark.parametrize(
    "changes",
    [
        {"active": "true"},
        {"tid": "other"},
        {"iss": "https://attacker.invalid"},
        {"aud": ["other"]},
        {"client_id": "other"},
        {"exp": 0},
        {"exp": True},
        {"nbf": int(time.time()) + 99999},
        {"sub": "codebuddy"},
    ],
)
async def test_invalid_enterprise_application_time_or_actor_is_rejected(changes):
    from api.auth.workbuddy.types import WorkBuddyAuthenticationError

    client, calls = make_client(changes)
    async with client:
        with pytest.raises(WorkBuddyAuthenticationError):
            await client.verify_employee("personal-ticket")
    assert not any("/users/" in r.url.path for r in calls)


@pytest.mark.parametrize("changes", [{"status": "Disabled"}, {"union_id": "another-employee"}])
async def test_disabled_or_mismatched_directory_identity_is_rejected(changes):
    from api.auth.workbuddy.types import WorkBuddyAuthenticationError

    client, _ = make_client(user_overrides=changes)
    async with client:
        with pytest.raises(WorkBuddyAuthenticationError):
            await client.verify_employee("personal-ticket")


async def test_root_must_be_confirmed_and_equal_names_preserved():
    client, _ = make_client(dept_overrides={"2": {"department_parent_id": "3"}})
    async with client:
        result = await client.verify_employee("personal-ticket")
        assert result.paths[0].complete is False
        assert len(result.paths[0].hierarchy) == 2
    client, _ = make_client(dept_overrides={"2": {"department_parent_id": None}})
    async with client:
        result = await client.verify_employee("personal-ticket")
        assert result.paths[0].complete is True
        assert len(result.paths[0].hierarchy) == 2


async def test_credentials_are_never_logged_or_redirected(caplog):
    from api.auth.workbuddy.types import WorkBuddyProviderUnavailable

    client, calls = make_client(redirect=True)
    async with client:
        with pytest.raises(WorkBuddyProviderUnavailable):
            await client.verify_employee("personal-ticket")
    assert len(calls) == 1
    assert "personal-ticket" not in caplog.text
    assert "server-secret" not in caplog.text


@pytest.mark.parametrize("kind", ["oversize", "timeout", "bad_metadata", "unknown_root", "depth"])
async def test_provider_bounds_and_incomplete_hierarchies(kind):
    from api.auth.workbuddy.oneid_client import OneIDClient
    from api.auth.workbuddy.types import WorkBuddyProviderUnavailable

    original, calls = make_client()
    handler = original._client._transport.handler

    async def override(req):
        if kind == "oversize":
            return httpx.Response(200, content=b"x" * 262145)
        if kind == "timeout":
            raise httpx.ReadTimeout("hidden", request=req)
        res = await handler(req)
        if kind == "bad_metadata" and req.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={**res.json(), "introspection_endpoint": "https://attacker.invalid"})
        if kind == "unknown_root" and req.url.path.endswith("/roots"):
            return httpx.Response(200, json={"code": 0, "data": {"roots": []}})
        if kind == "depth" and "/departments/" in req.url.path:
            dept = req.url.path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"code": 0, "data": {"department": {"department_id": dept, "department_name": "dept", "department_parent_id": str(int(dept) + 1)}}})
        return res

    async with original:
        async with OneIDClient(original.config, transport=httpx.MockTransport(override)) as client:
            if kind in ("unknown_root", "depth"):
                result = await client.verify_employee("personal-ticket")
                assert result.paths[0].complete is False
                assert len(result.paths[0].hierarchy) <= 24
            else:
                with pytest.raises(WorkBuddyProviderUnavailable):
                    await client.verify_employee("personal-ticket")
