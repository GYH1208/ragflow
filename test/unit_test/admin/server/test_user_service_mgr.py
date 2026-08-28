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
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace


def _load_admin_services(monkeypatch):
    config_module = types.ModuleType("config")
    config_module.SERVICE_CONFIGS = SimpleNamespace(configs=[])
    monkeypatch.setitem(sys.modules, "config", config_module)

    module_path = Path(__file__).parents[4] / "admin" / "server" / "services.py"
    spec = importlib.util.spec_from_file_location("admin_server_services_for_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_get_user_datasets_passes_active_team_ids_to_kb_service(monkeypatch):
    services = _load_admin_services(monkeypatch)
    user = SimpleNamespace(id="user-1")
    captured = []

    monkeypatch.setattr(services.UserService, "query_user_by_email", lambda _username: [user])
    monkeypatch.setattr(
        services.TenantService,
        "get_joined_tenants_by_user_id",
        lambda _user_id: [{"tenant_id": "legacy-owner"}],
    )
    monkeypatch.setattr(
        services,
        "TeamMemberService",
        SimpleNamespace(active_team_ids=lambda _user_id: ["team-active"]),
        raising=False,
    )
    monkeypatch.setattr(
        services.KnowledgebaseService,
        "get_all_kb_by_tenant_ids",
        lambda active_team_ids, user_id: captured.append((active_team_ids, user_id)) or ["dataset"],
    )

    assert services.UserServiceMgr.get_user_datasets("user@example.com") == ["dataset"]
    assert captured == [(["team-active"], "user-1")]
