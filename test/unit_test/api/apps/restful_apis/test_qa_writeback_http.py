"""Real Quart HTTP requests through the existing login_required guard."""

import ast
import importlib.util
import os
import sys
import time
from functools import wraps
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quart import Blueprint, Quart, current_app, g, jsonify, request
from quart_auth import Unauthorized

from api.db import APIKeyType
from common.constants import RetCode

ROOT = Path(__file__).resolve().parents[5]
URL = "/api/v1/datasets/kb/documents/doc/qa"
HEADERS = {"Authorization": "Bearer full-key"}
PAYLOAD = {"question": "问题", "answer": "第一行\n第二行", "source_system": "oa", "external_id": "external-1"}


@pytest.fixture
def setup(monkeypatch):
    service_spec = importlib.util.spec_from_file_location("api.apps.services.qa_writeback_service", ROOT / "api/apps/services/qa_writeback_service.py")
    service = importlib.util.module_from_spec(service_spec)
    monkeypatch.setitem(sys.modules, service_spec.name, service)
    service_spec.loader.exec_module(service)
    state = SimpleNamespace(
        kb=SimpleNamespace(id="kb", tenant_id="owner", status="1"),
        doc=SimpleNamespace(id="doc", kb_id="kb", status="1", parser_id="qa", run="0"),
        allowed=True,
    )

    def load_user(auth_types):
        assert set(auth_types) == {"API"}
        token = request.headers.get("Authorization")
        if token not in ("Bearer full-key", "Bearer readonly-key"):
            return None
        g.user = SimpleNamespace(id="caller")
        g.api_key_context = SimpleNamespace(key_type=APIKeyType.RETRIEVAL if token.endswith("readonly-key") else APIKeyType.FULL_ACCESS)
        return g.user

    guard_tree = ast.parse((ROOT / "api/apps/__init__.py").read_text())
    guard = next(n for n in guard_tree.body if isinstance(n, ast.FunctionDef) and n.name == "login_required")
    ns = {
        "wraps": wraps,
        "os": os,
        "time": time,
        "g": g,
        "request": request,
        "current_app": current_app,
        "_load_user": load_user,
        "_normalize_auth_types": lambda types: set(types),
        "AUTH_BETA": "BETA",
        "QuartAuthUnauthorized": Unauthorized,
        "APIKeyType": APIKeyType,
        "RetCode": RetCode,
        "KNOWLEDGE_RETRIEVE_SCOPE": "knowledge:retrieve",
        "get_json_result": lambda **kw: jsonify(kw),
    }
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    tree = ast.fix_missing_locations(ast.Module(body=[future, guard], type_ignores=[]))
    exec(compile(tree, "api/apps/__init__.py", "exec"), ns)  # noqa: S102 - execute trusted repository guard only
    apps = ModuleType("api.apps")
    apps.AUTH_API, apps.login_required = "API", ns["login_required"]
    monkeypatch.setitem(sys.modules, "api.apps", apps)
    permission = ModuleType("api.common.check_team_permission")
    permission.check_kb_team_permission = lambda kb, caller: state.allowed and caller == "caller"
    monkeypatch.setitem(sys.modules, permission.__name__, permission)
    kb_mod = ModuleType("api.db.services.knowledgebase_service")
    kb_mod.KnowledgebaseService = SimpleNamespace(get_by_id=lambda id: (bool(state.kb), state.kb))
    monkeypatch.setitem(sys.modules, kb_mod.__name__, kb_mod)
    doc_mod = ModuleType("api.db.services.document_service")
    doc_mod.DocumentService = SimpleNamespace(query=lambda **kw: [state.doc] if state.doc and state.doc.id == kw["id"] and state.doc.kb_id == kw["kb_id"] and state.doc.status == "1" else [])
    monkeypatch.setitem(sys.modules, doc_mod.__name__, doc_mod)
    route_spec = importlib.util.spec_from_file_location("qa_route_under_test", ROOT / "api/apps/restful_apis/qa_api.py")
    route = importlib.util.module_from_spec(route_spec)
    route.manager = Blueprint("qa", __name__)
    route_spec.loader.exec_module(route)
    create = AsyncMock(return_value={"chunk_id": "chunk", "created": True, "important_keywords": ["关键词"], "keyword_status": "generated"})
    monkeypatch.setattr(route, "QAWritebackService", lambda: SimpleNamespace(create=create))
    app = Quart(__name__)
    app.register_blueprint(route.manager, url_prefix="/api/v1")

    @app.errorhandler(Unauthorized)
    async def unauthorized(error):
        return jsonify(code=401, message="Unauthorized"), 401

    return app.test_client(), state, create, service


async def test_http_success_calls_service_with_original_owner(setup):
    client, state, create, _ = setup
    response = await client.post(URL, headers=HEADERS, json=PAYLOAD)
    assert response.status_code == 200
    body = await response.get_json()
    assert body["code"] == 0 and body["data"]["keyword_status"] == "generated"
    assert create.call_args.args == (state.kb, state.doc, PAYLOAD)


@pytest.mark.parametrize("headers,status", [({}, 401), ({"Authorization": "Bearer bad"}, 401), ({"Authorization": "Bearer readonly-key"}, 403)])
async def test_authentication_and_readonly_key_denied(setup, headers, status):
    client, _, create, _ = setup
    response = await client.post(URL, headers=headers, json=PAYLOAD)
    assert response.status_code == status
    create.assert_not_called()


@pytest.mark.parametrize(
    "case,status,error",
    [
        ("missing_kb", 404, "TARGET_NOT_FOUND"),
        ("forbidden", 404, "TARGET_NOT_FOUND"),
        ("wrong_doc", 404, "TARGET_NOT_FOUND"),
        ("inactive", 404, "TARGET_NOT_FOUND"),
        ("not_qa", 400, "INVALID_DOCUMENT_TYPE"),
        ("busy", 409, "DOCUMENT_BUSY"),
    ],
)
async def test_target_checks_before_models(setup, case, status, error):
    client, state, create, _ = setup
    if case == "missing_kb":
        state.kb = None
    elif case == "forbidden":
        state.allowed = False
    elif case == "wrong_doc":
        state.doc.kb_id = "other"
    elif case == "inactive":
        state.doc.status = "0"
    elif case == "not_qa":
        state.doc.parser_id = "naive"
    else:
        state.doc.run = "1"
    response = await client.post(URL, headers=HEADERS, json=PAYLOAD)
    assert response.status_code == status
    assert (await response.get_json())["data"]["error_code"] == error
    create.assert_not_called()


@pytest.mark.parametrize("body", [[], None, {**PAYLOAD, "answer": ""}, {**PAYLOAD, "keyword_top_n": True}])
async def test_invalid_json_values(setup, body):
    client, _, create, _ = setup
    response = await client.post(URL, headers=HEADERS, json=body)
    assert response.status_code == 400
    assert (await response.get_json())["code"] == 101
    create.assert_not_called()


async def test_malformed_json_and_wrong_content_type(setup):
    client, _, create, _ = setup
    for content_type, body in [("application/json", "{"), ("text/plain", "text")]:
        response = await client.post(URL, headers={**HEADERS, "Content-Type": content_type}, data=body)
        assert response.status_code == 400
    create.assert_not_called()


async def test_service_errors_keep_retry_contract(setup):
    client, _, create, service = setup
    create.side_effect = service.QAWritebackError(503, 503, "EMBEDDING_UNAVAILABLE", "Try again.", True)
    response = await client.post(URL, headers=HEADERS, json=PAYLOAD)
    assert response.status_code == 503
    assert (await response.get_json()) == {"code": 503, "message": "Try again.", "data": {"error_code": "EMBEDDING_UNAVAILABLE", "retryable": True}}
