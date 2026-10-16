"""Exercise cache boundaries against the real verifier and controlled OneID HTTP."""

import asyncio
from collections import Counter
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from api.auth.workbuddy import oneid_client
from api.auth.workbuddy.config import WorkBuddyConfig
from api.auth.workbuddy.types import WorkBuddyAuthenticationError, WorkBuddyProviderUnavailable

ISSUER = "https://oauth2.account.tencent.com/authz/oidc/v2/123"


@pytest.fixture
def provider(monkeypatch):
    state = SimpleNamespace(wall=1000, mono=100, calls=Counter(), disabled=False, department="3", fail=None, block=None)
    monkeypatch.setattr(oneid_client, "time", SimpleNamespace(time=lambda: state.wall, monotonic=lambda: state.mono))
    monkeypatch.delenv("RAGFLOW_WORKBUDDY_TOKEN_TIMING_UNTIL", raising=False)
    state.tickets = {
        "old-ticket": {"sub": "employee-1", "exp": 2800},
        "new-ticket": {"sub": "employee-1", "exp": 4600},
        "other-ticket": {"sub": "employee-2", "exp": 4600},
    }
    state.config = WorkBuddyConfig(True, ISSUER, "123", "i-app", "app-secret")

    async def handler(request):
        path = request.url.path
        state.calls[path] += 1
        if state.block and path.endswith(state.block[0]):
            state.block[1].set()
            await state.block[2].wait()
        if state.fail and path.endswith(state.fail):
            return httpx.Response(503)
        if path.endswith("openid-configuration"):
            data = {
                "issuer": ISSUER,
                "introspection_endpoint": ISSUER + "/introspect",
                "userinfo_endpoint": ISSUER + "/userinfo",
                "introspection_endpoint_auth_methods_supported": ["client_secret_post"],
            }
        elif path.endswith("/introspect"):
            token = parse_qs(request.content.decode())["token"][0]
            data = {"active": True, "client_id": "codebuddy", "aud": ["i-app"], "iss": ISSUER, "tid": "123", "iat": 900, **state.tickets.get(token, {"active": False})}
        elif path.endswith("/userinfo"):
            token = request.headers["Authorization"].removeprefix("Bearer ")
            data = {"sub": state.tickets[token]["sub"], "tid": "123", "name": "Employee"}
        elif path.endswith("/token"):
            data = {"access_token": "application-ticket", "expires_in": 600}
        elif "/users/" in path:
            subject = path.rsplit("/", 1)[-1]
            data = {
                "code": 0,
                "data": {
                    "user": {
                        "union_id": subject,
                        "name": subject,
                        "status": "Disabled" if state.disabled else "Enabled",
                        "departments": [{"department_id": state.department, "is_main_department": True}],
                    }
                },
            }
        else:
            dept = path.rsplit("/", 1)[-1]
            data = {"code": 0, "data": {"department": {"department_id": dept, "department_name": "Department", "department_parent_id": None if dept == "2" else "2"}}}
        return httpx.Response(200, json=data)

    state.client = lambda **settings: oneid_client.OneIDClient(replace(state.config, **settings), transport=httpx.MockTransport(handler))
    return state


async def test_repeated_calls_reuse_verified_ticket_and_directory(provider):
    async with provider.client() as client:
        profiles = [await client.verify_employee("old-ticket") for _ in range(3)]
    assert all(p.union_id == "employee-1" for p in profiles)
    assert provider.calls[ISSUER.removeprefix("https://oauth2.account.tencent.com") + "/introspect"] == 1
    assert sum(n for path, n in provider.calls.items() if path.endswith("/userinfo")) == 1
    assert sum(n for path, n in provider.calls.items() if "/users/" in path) == 1
    assert sum(n for path, n in provider.calls.items() if "/departments/" in path) == 2


async def test_new_ticket_verifies_identity_but_reuses_employee_profile(provider):
    async with provider.client() as client:
        first = await client.verify_employee("old-ticket")
        second = await client.verify_employee("new-ticket")
        other = await client.verify_employee("other-ticket")
        with pytest.raises(WorkBuddyAuthenticationError):
            await client.verify_employee("forged-ticket")
    assert first == second
    assert other.union_id == "employee-2"
    assert sum(n for path, n in provider.calls.items() if path.endswith("/introspect")) == 4
    assert sum(n for path, n in provider.calls.items() if path.endswith("/userinfo")) == 3
    assert sum(n for path, n in provider.calls.items() if "/users/" in path) == 2


async def test_expiry_is_checked_even_with_a_warm_profile(provider):
    async with provider.client() as client:
        await client.verify_employee("old-ticket")
        provider.wall = 2800
        provider.mono += 1800
        with pytest.raises(WorkBuddyAuthenticationError):
            await client.verify_employee("old-ticket")
        assert (await client.verify_employee("new-ticket")).union_id == "employee-1"
    assert sum(n for path, n in provider.calls.items() if "/users/" in path) == 1


async def test_profile_expiry_is_independent_of_new_ticket_and_hits_do_not_extend_it(provider):
    provider.tickets["old-ticket"]["exp"] = 200000
    provider.tickets["new-ticket"]["exp"] = 200000
    async with provider.client() as client:
        assert (await client.verify_employee("old-ticket")).paths[0].department_id == "3"
        provider.department = "4"
        provider.wall += 86399
        provider.mono += 86399
        assert (await client.verify_employee("new-ticket")).paths[0].department_id == "3"
        provider.wall += 1
        provider.mono += 1
        assert (await client.verify_employee("new-ticket")).paths[0].department_id == "4"
    assert sum(n for path, n in provider.calls.items() if "/users/" in path) == 2
    assert sum(n for path, n in provider.calls.items() if path.endswith("/introspect")) == 2


async def test_token_validation_refreshes_at_24_hours_even_for_long_lived_ticket(provider):
    provider.tickets["old-ticket"]["exp"] = 200000
    async with provider.client() as client:
        await client.verify_employee("old-ticket")
        provider.wall += 86400
        provider.mono += 86400
        await client.verify_employee("old-ticket")
    assert sum(n for path, n in provider.calls.items() if path.endswith("/introspect")) == 2


async def test_clock_rollback_does_not_extend_ticket_cache(provider):
    async with provider.client() as client:
        await client.verify_employee("old-ticket")
        provider.wall -= 100
        provider.mono += 1800
        provider.tickets["old-ticket"]["active"] = False
        with pytest.raises(WorkBuddyAuthenticationError):
            await client.verify_employee("old-ticket")


async def test_expired_profile_cannot_fall_back_when_directory_is_down(provider):
    async with provider.client(profile_cache_ttl_seconds=10) as client:
        await client.verify_employee("old-ticket")
        provider.fail = "/users/employee-1"
        provider.wall += 10
        provider.mono += 10
        with pytest.raises(WorkBuddyProviderUnavailable):
            await client.verify_employee("old-ticket")
        provider.fail = None
        provider.disabled = True
        with pytest.raises(WorkBuddyAuthenticationError):
            await client.verify_employee("old-ticket")
        provider.disabled = False
        assert (await client.verify_employee("old-ticket")).union_id == "employee-1"


async def test_incomplete_department_lookup_is_not_cached_for_a_day(provider):
    provider.fail = "/departments/3"
    async with provider.client() as client:
        assert not (await client.verify_employee("old-ticket")).paths[0].complete
        provider.fail = None
        assert (await client.verify_employee("old-ticket")).paths[0].complete


@pytest.mark.parametrize("same_ticket", [True, False])
async def test_concurrent_calls_share_ticket_or_profile_refresh(provider, same_ticket):
    entered, release = asyncio.Event(), asyncio.Event()
    provider.block = ("/users/employee-1", entered, release)
    async with provider.client() as client:
        tasks = [asyncio.create_task(client.verify_employee("old-ticket" if same_ticket or i % 2 == 0 else "new-ticket")) for i in range(12)]
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.sleep(0)
        release.set()
        profiles = await asyncio.gather(*tasks)
    assert all(p.union_id == "employee-1" for p in profiles)
    assert sum(n for path, n in provider.calls.items() if "/users/" in path) == 1
    assert sum(n for path, n in provider.calls.items() if path.endswith("/introspect")) == (1 if same_ticket else 2)


async def test_cancelled_refresh_does_not_poison_next_request(provider):
    entered, release = asyncio.Event(), asyncio.Event()
    provider.block = ("/users/employee-1", entered, release)
    async with provider.client() as client:
        task = asyncio.create_task(client.verify_employee("old-ticket"))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert (await asyncio.wait_for(client.verify_employee("old-ticket"), 2)).union_id == "employee-1"


async def test_ticket_expiring_during_directory_fetch_is_not_accepted(provider):
    entered, release = asyncio.Event(), asyncio.Event()
    provider.block = ("/users/employee-1", entered, release)
    async with provider.client() as client:
        task = asyncio.create_task(client.verify_employee("old-ticket"))
        await asyncio.wait_for(entered.wait(), 2)
        provider.wall = 2800
        provider.mono += 1800
        release.set()
        with pytest.raises(WorkBuddyAuthenticationError):
            await task


async def test_validation_ttl_expiring_during_profile_refresh_is_not_accepted(provider):
    async with provider.client(token_cache_ttl_seconds=10, profile_cache_ttl_seconds=0) as client:
        await client.verify_employee("old-ticket")
        provider.wall += 9
        provider.mono += 9
        provider.tickets["old-ticket"]["active"] = False
        entered, release = asyncio.Event(), asyncio.Event()
        provider.block = ("/users/employee-1", entered, release)
        task = asyncio.create_task(client.verify_employee("old-ticket"))
        await asyncio.wait_for(entered.wait(), 2)
        provider.wall += 2
        provider.mono += 2
        release.set()
        with pytest.raises(WorkBuddyAuthenticationError):
            await task
        with pytest.raises(WorkBuddyAuthenticationError):
            await client.verify_employee("old-ticket")


async def test_cache_disabled_and_new_client_require_online_checks(provider):
    async with provider.client(token_cache_ttl_seconds=0, profile_cache_ttl_seconds=0) as client:
        await client.verify_employee("old-ticket")
        await client.verify_employee("old-ticket")
    async with provider.client() as client:
        await client.verify_employee("old-ticket")
    assert sum(n for path, n in provider.calls.items() if path.endswith("/introspect")) == 3
    assert sum(n for path, n in provider.calls.items() if "/users/" in path) == 3


async def test_cache_eviction_rechecks_identity_without_cross_employee_leak(provider, monkeypatch):
    monkeypatch.setattr(oneid_client.OneIDClient, "CACHE_MAX_ENTRIES", 2)
    async with provider.client() as client:
        for i in range(3):
            provider.tickets[f"ticket-{i}"] = {"sub": f"employee-{i}", "exp": 2800}
            assert (await client.verify_employee(f"ticket-{i}")).union_id == f"employee-{i}"
        assert (await client.verify_employee("ticket-0")).union_id == "employee-0"
    assert sum(n for path, n in provider.calls.items() if path.endswith("/introspect")) == 4
    assert sum(n for path, n in provider.calls.items() if "/users/" in path) == 4


async def test_warm_identity_cache_still_reads_local_disable_and_center_grants(provider, tmp_path):
    from peewee import SqliteDatabase
    from quart import Quart
    from werkzeug.exceptions import Forbidden

    from api.apps.workbuddy_auth import authenticate_workbuddy
    from api.db import db_models as models
    from api.db.services.center_dataset_service import CenterDatasetService as Centers
    from api.db.services.workbuddy_employee_service import WorkBuddyEmployeeService as Employees

    tables = [models.User, models.WorkBuddyEmployeeIdentity, models.EnterpriseDepartment, models.CenterDatasetGrant, models.Knowledgebase]
    database = SqliteDatabase(str(tmp_path / "employees.db"))
    app = Quart("cached-employee-authorization")
    with database.bind_ctx(tables):
        with database.connection_context():
            database.create_tables(tables)
            models.Knowledgebase.create(id="kb-a", tenant_id="owner", name="A", embd_id="model", created_by="owner")
        async with provider.client() as client, app.app_context():
            app.extensions["workbuddy_oneid"] = client
            user, context = await authenticate_workbuddy("old-ticket")
            assert not context.allowed_dataset_ids
            with database.connection_context():
                Centers.set_center("123", "2", True, "admin")
                Centers.replace_grants("123", "2", ["kb-a"], "admin")
            assert (await authenticate_workbuddy("old-ticket"))[1].allowed_dataset_ids == frozenset({"kb-a"})
            with database.connection_context():
                Centers.replace_grants("123", "2", [], "admin")
            assert not (await authenticate_workbuddy("old-ticket"))[1].allowed_dataset_ids
            with database.connection_context():
                Centers.replace_grants("123", "2", ["kb-a"], "admin")
                Centers.set_center("123", "2", False, "admin")
            assert not (await authenticate_workbuddy("old-ticket"))[1].allowed_dataset_ids
            with database.connection_context():
                Employees.set_enabled(user.id, False, "admin")
            with pytest.raises(Forbidden):
                await authenticate_workbuddy("old-ticket")
            with database.connection_context():
                assert models.User.get_by_id(user.id).is_active == "0"
                assert models.WorkBuddyEmployeeIdentity.select().count() == 1
    assert sum(n for path, n in provider.calls.items() if path.endswith("/introspect")) == 1
    assert sum(n for path, n in provider.calls.items() if "/users/" in path) == 1
