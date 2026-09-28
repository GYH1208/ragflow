#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from werkzeug.exceptions import Unauthorized as WerkzeugUnauthorized

from api.db import APIKeyType


class _DummyAPIToken:
    @staticmethod
    def query(**_kwargs):
        return []


class _DummyScopedAPIToken:
    @staticmethod
    def query(**_kwargs):
        return []


class _DummyUserService:
    @staticmethod
    def query(**_kwargs):
        return []


class _DummyAPIKeyUsageService:
    @staticmethod
    def record(**_kwargs):
        return True


def _run(coro):
    return asyncio.run(coro)


def _load_apps_module(monkeypatch):
    repo_root = Path(__file__).resolve().parents[4]

    common_pkg = ModuleType("common")
    common_pkg.__path__ = [str(repo_root / "common")]
    monkeypatch.setitem(sys.modules, "common", common_pkg)

    settings_mod = ModuleType("common.settings")
    settings_mod.SECRET_KEY = "test-secret-key"
    settings_mod.get_secret_key = lambda: "test-secret-key"
    settings_mod.init_settings = lambda: None
    settings_mod.decrypt_database_config = lambda name=None: {}
    monkeypatch.setitem(sys.modules, "common.settings", settings_mod)
    common_pkg.settings = settings_mod

    db_models_mod = ModuleType("api.db.db_models")
    db_models_mod.APIToken = _DummyAPIToken
    db_models_mod.ScopedAPIToken = _DummyScopedAPIToken
    db_models_mod.close_connection = lambda: None
    monkeypatch.setitem(sys.modules, "api.db.db_models", db_models_mod)

    services_mod = ModuleType("api.db.services")
    services_mod.__path__ = []
    services_mod.UserService = _DummyUserService
    monkeypatch.setitem(sys.modules, "api.db.services", services_mod)

    api_service_mod = ModuleType("api.db.services.api_service")
    api_service_mod.APIKeyUsageService = _DummyAPIKeyUsageService
    monkeypatch.setitem(sys.modules, "api.db.services.api_service", api_service_mod)

    commands_mod = ModuleType("api.utils.commands")
    commands_mod.register_commands = lambda _app: None
    monkeypatch.setitem(sys.modules, "api.utils.commands", commands_mod)

    api_utils_mod = ModuleType("api.utils.api_utils")

    def _get_json_result(code=0, message="success", data=None):
        return {"code": code, "message": message, "data": data}

    def _server_error_response(error):
        return {"code": 100, "message": repr(error)}

    api_utils_mod.get_json_result = _get_json_result
    api_utils_mod.server_error_response = _server_error_response
    monkeypatch.setitem(sys.modules, "api.utils.api_utils", api_utils_mod)

    backward_compat_mod = ModuleType("api.apps.backward_compat")
    backward_compat_mod.register_backward_compat_routes = lambda _app: None
    monkeypatch.setitem(sys.modules, "api.apps.backward_compat", backward_compat_mod)

    api_key_auth_name = "api.apps.api_key_auth"
    api_key_auth_path = repo_root / "api" / "apps" / "api_key_auth.py"
    api_key_auth_spec = importlib.util.spec_from_file_location(api_key_auth_name, api_key_auth_path)
    api_key_auth_module = importlib.util.module_from_spec(api_key_auth_spec)
    monkeypatch.setitem(sys.modules, api_key_auth_name, api_key_auth_module)
    api_key_auth_spec.loader.exec_module(api_key_auth_module)

    module_name = "test_apps_init_unit_module"
    module_path = repo_root / "api" / "apps" / "__init__.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)

    monkeypatch.setattr(Path, "glob", lambda self, _pattern: [])
    spec.loader.exec_module(module)
    return module.app, module


@pytest.mark.p2
def test_module_init_and_unauthorized_message_variants(monkeypatch):
    _quart_app, apps_module = _load_apps_module(monkeypatch)

    assert apps_module.client_urls_prefix == []

    class _BrokenRepr:
        def __repr__(self):
            raise RuntimeError("repr explode")

    class _ExactUnauthorizedRepr:
        def __repr__(self):
            return apps_module.UNAUTHORIZED_MESSAGE

    class _Unauthorized401Repr:
        def __repr__(self):
            return "Unauthorized 401 from upstream"

    class _WithDescription:
        description = "Custom description"

    assert apps_module._unauthorized_message(None) == apps_module.UNAUTHORIZED_MESSAGE
    assert apps_module._unauthorized_message(_BrokenRepr()) == apps_module.UNAUTHORIZED_MESSAGE
    assert apps_module._unauthorized_message(_ExactUnauthorizedRepr()) == apps_module.UNAUTHORIZED_MESSAGE
    assert apps_module._unauthorized_message(_Unauthorized401Repr()) == "Unauthorized 401 from upstream"
    assert apps_module._unauthorized_message(_WithDescription()) == "Custom description"


@pytest.mark.p2
def test_load_user_token_edge_cases(monkeypatch):
    quart_app, apps_module = _load_apps_module(monkeypatch)

    user_with_empty_token = SimpleNamespace(email="empty@example.com", access_token="")

    async def _case():
        async with quart_app.test_request_context("/", headers={"Authorization": "token"}):
            monkeypatch.setattr(apps_module.Serializer, "loads", lambda _self, _auth: "")
            assert apps_module._load_user() is None

        async with quart_app.test_request_context("/", headers={"Authorization": "token"}):
            monkeypatch.setattr(apps_module.Serializer, "loads", lambda _self, _auth: "short-token")
            assert apps_module._load_user() is None

        async with quart_app.test_request_context("/", headers={"Authorization": "token"}):
            monkeypatch.setattr(apps_module.Serializer, "loads", lambda _self, _auth: "a" * 32)
            monkeypatch.setattr(apps_module.UserService, "query", lambda **_kwargs: [user_with_empty_token])
            assert apps_module._load_user() is None

    _run(_case())


@pytest.mark.p2
def test_load_user_api_token_fallback_and_fallback_exception(monkeypatch, caplog):
    quart_app, apps_module = _load_apps_module(monkeypatch)

    def _raise_decode(_self, _auth):
        raise RuntimeError("decode failed")

    monkeypatch.setattr(apps_module.Serializer, "loads", _raise_decode)

    fallback_user_empty_token = SimpleNamespace(email="fallback@example.com", access_token="")
    valid_token = "a" * 32
    beta_user = SimpleNamespace(
        id="tenant-1",
        email="embed@example.com",
        access_token=valid_token,
    )

    async def _case():
        monkeypatch.setattr(apps_module.APIToken, "query", lambda **_kwargs: [SimpleNamespace(tenant_id="tenant-1")])
        monkeypatch.setattr(apps_module.UserService, "query", lambda **_kwargs: [fallback_user_empty_token])
        async with quart_app.test_request_context("/", headers={"Authorization": "Bearer api-token"}):
            assert apps_module._load_user() is None

        secret_api_token = "ragflow-full-secret-value"

        def _raise_api_token(**_kwargs):
            raise RuntimeError(f"api token fallback failed for {secret_api_token}")

        monkeypatch.setattr(apps_module.APIToken, "query", _raise_api_token)
        async with quart_app.test_request_context("/", headers={"Authorization": f"Bearer {secret_api_token}"}):
            with caplog.at_level(logging.WARNING):
                assert apps_module._load_user() is None

        def _query_api_token(**kwargs):
            if kwargs.get("beta") == "embed-beta":
                return [SimpleNamespace(tenant_id="tenant-1")]
            return []

        def _query_user(**kwargs):
            if (
                kwargs.get("id") == "tenant-1"
                and kwargs.get("status") == apps_module.StatusEnum.VALID.value
            ):
                return [beta_user]
            return []

        monkeypatch.setattr(apps_module.APIToken, "query", _query_api_token)
        monkeypatch.setattr(apps_module.UserService, "query", _query_user)
        async with quart_app.test_request_context("/", headers={"Authorization": "Bearer embed-beta"}):
            user = apps_module._load_user(auth_types=[apps_module.AUTH_BETA])
            assert user is beta_user
            assert apps_module.g.auth_type == apps_module.AUTH_BETA

        async with quart_app.test_request_context("/", headers={"Authorization": "Bearer invalid-beta"}):
            assert apps_module._load_user(auth_types=[apps_module.AUTH_BETA]) is None

    _run(_case())
    assert "load_user from API key failed" in caplog.text
    assert "ragflow-full-secret-value" not in caplog.text


@pytest.mark.p2
def test_load_user_session_fallback(monkeypatch, caplog):
    quart_app, apps_module = _load_apps_module(monkeypatch)

    valid_token = "a" * 32
    valid_user = SimpleNamespace(id="user-1", email="oidc@example.com", access_token=valid_token)
    invalid_token_user = SimpleNamespace(id="user-1", email="oidc@example.com", access_token="INVALID_deadbeef")
    short_token_user = SimpleNamespace(id="user-1", email="oidc@example.com", access_token="too-short")

    async def _case():
        # No Authorization header but a valid session: helper resolves the user.
        async with quart_app.test_request_context("/"):
            from quart import session

            session["_user_id"] = "user-1"
            monkeypatch.setattr(apps_module.UserService, "query", lambda **_kw: [valid_user])
            assert apps_module._load_user() is valid_user

        # Malformed bearer header still falls back to session.
        async with quart_app.test_request_context("/", headers={"Authorization": "Bearer"}):
            from quart import session

            session["_user_id"] = "user-1"
            monkeypatch.setattr(apps_module.UserService, "query", lambda **_kw: [valid_user])
            assert apps_module._load_user() is valid_user

        # Logout-revoked tokens (INVALID_ prefix) are rejected even with a session.
        async with quart_app.test_request_context("/"):
            from quart import session

            session["_user_id"] = "user-1"
            monkeypatch.setattr(apps_module.UserService, "query", lambda **_kw: [invalid_token_user])
            assert apps_module._load_user() is None

        # Short tokens are rejected (matches the JWT-path length floor).
        async with quart_app.test_request_context("/"):
            from quart import session

            session["_user_id"] = "user-1"
            monkeypatch.setattr(apps_module.UserService, "query", lambda **_kw: [short_token_user])
            assert apps_module._load_user() is None

        # No session and no header → still None.
        async with quart_app.test_request_context("/"):
            assert apps_module._load_user() is None

        # Database errors during the session lookup are swallowed and logged.
        async with quart_app.test_request_context("/"):
            from quart import session

            session["_user_id"] = "user-1"

            def _raise(**_kw):
                raise RuntimeError("db down")

            monkeypatch.setattr(apps_module.UserService, "query", _raise)
            with caplog.at_level(logging.ERROR):
                assert apps_module._load_user() is None

    _run(_case())
    assert "load_user from session failed" in caplog.text


@pytest.mark.p2
def test_load_user_session_fallback_after_token_paths_fail(monkeypatch):
    """JWT-decode failures and API-token exhaustion must still fall through
    to the session and return the user, not None."""
    quart_app, apps_module = _load_apps_module(monkeypatch)

    valid_token = "b" * 32
    valid_user = SimpleNamespace(id="user-1", email="oidc@example.com", access_token=valid_token)

    def _raise_decode(_self, _auth):
        raise RuntimeError("jwt decode boom")

    monkeypatch.setattr(apps_module.Serializer, "loads", _raise_decode)
    monkeypatch.setattr(apps_module.APIToken, "query", lambda **_kw: [])

    async def _case():
        # JWT decode fails AND API-token query returns nothing → session wins.
        async with quart_app.test_request_context("/", headers={"Authorization": "Bearer junk"}):
            from quart import session

            session["_user_id"] = "user-1"
            monkeypatch.setattr(apps_module.UserService, "query", lambda **_kw: [valid_user])
            assert apps_module._load_user() is valid_user

    _run(_case())


@pytest.mark.p2
def test_login_required_preserves_jwt_and_full_access_execution_and_sets_key_context(monkeypatch):
    quart_app, apps_module = _load_apps_module(monkeypatch)

    valid_access_token = "j" * 32
    jwt_user = SimpleNamespace(id="jwt-user", email="jwt@example.com", access_token=valid_access_token)
    full_user = SimpleNamespace(id="tenant-full", email="full@example.com", access_token="f" * 32)
    full_record = SimpleNamespace(tenant_id="tenant-full", token="ragflow-full-1", enabled=True)

    def _query_user(**kwargs):
        if kwargs.get("access_token") == valid_access_token:
            return [jwt_user]
        if kwargs.get("id") == "tenant-full":
            return [full_user]
        return []

    monkeypatch.setattr(apps_module.UserService, "query", _query_user)

    @apps_module.login_required
    async def _handler():
        context = getattr(apps_module.g, "api_key_context", None)
        return {
            "auth_type": apps_module.g.auth_type,
            "key_type": context.key_type.value if context else None,
            "tenant_id": context.tenant_id if context else None,
        }

    async def _case():
        monkeypatch.setattr(apps_module.Serializer, "loads", lambda _self, _auth: valid_access_token)
        async with quart_app.test_request_context("/jwt", headers={"Authorization": "Bearer signed-jwt"}):
            assert await _handler() == {
                "auth_type": apps_module.AUTH_JWT,
                "key_type": None,
                "tenant_id": None,
            }

        def _raise_decode(_self, _auth):
            raise RuntimeError("not jwt")

        monkeypatch.setattr(apps_module.Serializer, "loads", _raise_decode)
        monkeypatch.setattr(apps_module.APIToken, "query", lambda **_kwargs: [full_record])
        async with quart_app.test_request_context("/full", headers={"Authorization": "Bearer ragflow-full-1"}):
            assert await _handler() == {
                "auth_type": apps_module.AUTH_API,
                "key_type": "full_access",
                "tenant_id": "tenant-full",
            }

    _run(_case())


@pytest.mark.p2
def test_login_required_allows_retrieval_key_only_for_exact_declared_scope(monkeypatch):
    quart_app, apps_module = _load_apps_module(monkeypatch)

    scoped_record = SimpleNamespace(
        tenant_id="tenant-scoped",
        token="ragflow-rk-valid",
        enabled=True,
        expires_at=None,
        allowed_dataset_ids=["dataset-1"],
    )
    scoped_user = SimpleNamespace(id="tenant-scoped", email="scoped@example.com", access_token="s" * 32)
    monkeypatch.setattr(apps_module.Serializer, "loads", lambda _self, _auth: (_ for _ in ()).throw(RuntimeError("not jwt")))
    monkeypatch.setattr(_DummyScopedAPIToken, "query", lambda **_kwargs: [scoped_record])
    monkeypatch.setattr(apps_module.APIToken, "query", lambda **_kwargs: [])
    monkeypatch.setattr(apps_module.UserService, "query", lambda **_kwargs: [scoped_user])

    calls = []

    @apps_module.login_required(api_scope=apps_module.KNOWLEDGE_RETRIEVE_SCOPE)
    async def _retrieval_handler():
        calls.append("allowed")
        return {
            "key_type": apps_module.g.api_key_context.key_type.value,
            "datasets": sorted(apps_module.g.api_key_context.allowed_dataset_ids),
        }

    @apps_module.login_required
    async def _unscoped_handler():
        calls.append("unscoped")
        return {"ok": True}

    @apps_module.login_required(api_scope="knowledge:read")
    async def _wrong_scope_handler():
        calls.append("wrong-scope")
        return {"ok": True}

    async def _case():
        headers = {"Authorization": "Bearer ragflow-rk-valid"}
        async with quart_app.test_request_context("/retrieval", headers=headers):
            assert await _retrieval_handler() == {
                "key_type": "retrieval",
                "datasets": ["dataset-1"],
            }

        async with quart_app.test_request_context("/system", headers=headers):
            payload, status = await _unscoped_handler()
            assert status == 403
            assert payload["code"] == 403

        async with quart_app.test_request_context("/wrong", headers=headers):
            payload, status = await _wrong_scope_handler()
            assert status == 403
            assert payload["code"] == 403

    _run(_case())
    assert calls == ["allowed"]


@pytest.mark.p2
def test_dataset_access_forbidden_exception_is_rendered_as_http_403(monkeypatch):
    quart_app, _apps_module = _load_apps_module(monkeypatch)
    api_key_auth = sys.modules["api.apps.api_key_auth"]

    @quart_app.get("/dataset")
    async def _dataset_handler():
        from quart import g

        g.api_key_context = api_key_auth.APIKeyContext(
            key_type=APIKeyType.RETRIEVAL,
            tenant_id="tenant-scoped",
            token="ragflow-rk-valid",
            record=SimpleNamespace(token="ragflow-rk-valid"),
            allowed_dataset_ids=frozenset({"dataset-1"}),
        )
        api_key_auth.require_dataset_access(["dataset-outside-scope"])
        return {"ok": True}

    async def _case():
        response = await quart_app.test_client().get("/dataset")
        assert response.status_code == 403
        payload = await response.get_json()
        assert payload["code"] == 403

    _run(_case())


@pytest.mark.p2
def test_login_required_timing_and_login_user_inactive(monkeypatch, caplog):
    quart_app, apps_module = _load_apps_module(monkeypatch)

    monkeypatch.setenv("RAGFLOW_API_TIMING", "1")
    monkeypatch.setattr(apps_module, "_load_user", lambda _auth_types=None: SimpleNamespace(id="tenant-1"))

    @apps_module.login_required
    async def _timed_handler():
        return {"ok": True}

    async def _case():
        async with quart_app.test_request_context(
            "/api/v1/system/tokens",
            method="PATCH",
            json={"token": "ragflow-body-only-secret", "name": "Renamed"},
        ):
            with caplog.at_level(logging.INFO):
                assert await _timed_handler() == {"ok": True}

            inactive_user = SimpleNamespace(id="user-1", is_active=False)
            assert apps_module.login_user(inactive_user) is False

    _run(_case())
    assert "api_timing login_required" in caplog.text
    assert "path=/api/v1/system/tokens" in caplog.text
    assert "ragflow-body-only-secret" not in caplog.text


@pytest.mark.p2
def test_logout_user_not_found_and_unauthorized_handlers(monkeypatch, caplog):
    quart_app, apps_module = _load_apps_module(monkeypatch)

    async def _case():
        async with quart_app.test_request_context("/logout", headers={"Cookie": "remember_token=abc"}):
            from quart import session

            session["_user_id"] = "user-1"
            session["_fresh"] = True
            session["_id"] = "session-id"
            session["_remember_seconds"] = 5

            assert apps_module.logout_user() is True
            assert "_user_id" not in session
            assert "_fresh" not in session
            assert "_id" not in session
            assert session.get("_remember") == "clear"
            assert "_remember_seconds" not in session

        raw_key = "ragflow-secret-in-obsolete-management-path"
        async with quart_app.test_request_context(f"/api/v1/system/tokens/{raw_key}"):
            with caplog.at_level(logging.ERROR):
                not_found_resp, status = await apps_module.not_found(RuntimeError("missing"))
            assert status == apps_module.RetCode.NOT_FOUND
            payload = await not_found_resp.get_json()
            assert payload["code"] == apps_module.RetCode.NOT_FOUND
            assert payload["error"] == "Not Found"
            assert payload["message"] == "Not Found."
            assert raw_key not in payload["message"]
            assert raw_key not in caplog.text

        async with quart_app.test_request_context("/protected"):

            @apps_module.login_required
            async def _protected():
                return {"ok": True}

            monkeypatch.setattr(apps_module, "current_user", None)
            with pytest.raises(apps_module.QuartAuthUnauthorized) as exc_info:
                await _protected()

            quart_payload, quart_status = await apps_module.unauthorized_quart_auth(exc_info.value)
            assert quart_status == apps_module.RetCode.UNAUTHORIZED
            assert quart_payload["code"] == apps_module.RetCode.UNAUTHORIZED

            werk_payload, werk_status = await apps_module.unauthorized_werkzeug(WerkzeugUnauthorized("Unauthorized 401"))
            assert werk_status == apps_module.RetCode.UNAUTHORIZED
            assert werk_payload["code"] == apps_module.RetCode.UNAUTHORIZED

    _run(_case())
