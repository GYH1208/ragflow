import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from common.constants import RetCode


class _DummyManager:
    def route(self, *_args, **_kwargs):
        return lambda function: function


def _run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture()
def chat_analytics_api_module(monkeypatch):
    apps_stub = ModuleType("api.apps")
    apps_stub.current_user = SimpleNamespace(id="user-1")
    apps_stub.login_required = lambda function: function
    monkeypatch.setitem(sys.modules, "api.apps", apps_stub)
    module_path = (
        Path(__file__).resolve().parents[5]
        / "api"
        / "apps"
        / "restful_apis"
        / "chat_analytics_api.py"
    )
    spec = importlib.util.spec_from_file_location(
        "test_chat_analytics_api_unit", module_path
    )
    module = importlib.util.module_from_spec(spec)
    module.manager = _DummyManager()
    spec.loader.exec_module(module)
    return module


def _empty_dashboard():
    return {
        "total_count": 0,
        "last_30_days_count": 0,
        "today_count": 0,
        "trend": [],
        "assistants": [],
        "assistant_options": [],
    }


def test_chat_analytics_uses_current_tenant_and_defaults(
    chat_analytics_api_module, monkeypatch
):
    module = chat_analytics_api_module
    seen = {}
    monkeypatch.setattr(
        module.UserTenantService,
        "query",
        lambda **kwargs: [SimpleNamespace(tenant_id="tenant-1")],
    )

    def dashboard(**kwargs):
        seen.update(kwargs)
        return _empty_dashboard()

    monkeypatch.setattr(module.ChatAnalyticsService, "dashboard", dashboard)
    monkeypatch.setattr(module, "request", SimpleNamespace(args={}))

    result = _run(module.chat_analytics())

    assert result["code"] == 0
    assert result["data"]["total_count"] == 0
    assert seen["tenant_id"] == "tenant-1"
    assert seen["dialog_id"] is None
    assert seen["granularity"] == "day"
    assert seen["from_date"].time().isoformat() == "00:00:00"
    assert seen["to_date"].time().isoformat() == "23:59:59.999999"
    assert (seen["to_date"].date() - seen["from_date"].date()).days == 29


@pytest.mark.parametrize(
    "args",
    [
        {"granularity": "hour"},
        {"from_date": "bad"},
        {"from_date": "2026-09-25", "to_date": "2026-09-24"},
    ],
)
def test_chat_analytics_rejects_invalid_params(
    chat_analytics_api_module, monkeypatch, args
):
    module = chat_analytics_api_module
    monkeypatch.setattr(module, "request", SimpleNamespace(args=args))

    result = _run(module.chat_analytics())

    assert result["code"] == RetCode.DATA_ERROR


def test_chat_analytics_rejects_assistant_outside_tenant(
    chat_analytics_api_module, monkeypatch
):
    module = chat_analytics_api_module
    monkeypatch.setattr(
        module.UserTenantService,
        "query",
        lambda **kwargs: [SimpleNamespace(tenant_id="tenant-1")],
    )
    monkeypatch.setattr(
        module.ChatAnalyticsService,
        "dashboard",
        lambda **kwargs: (_ for _ in ()).throw(
            ValueError("Chat assistant not found")
        ),
    )
    monkeypatch.setattr(
        module,
        "request",
        SimpleNamespace(args={"dialog_id": "other-tenant-dialog"}),
    )

    result = _run(module.chat_analytics())

    assert result["code"] == RetCode.DATA_ERROR
    assert result["message"] == "Chat assistant not found"
    assert "data" not in result
