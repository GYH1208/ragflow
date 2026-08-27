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
from pathlib import Path
from types import SimpleNamespace

import pytest

import api.apps


class _DummyManager:
    def route(self, *_args, **_kwargs):
        return lambda function: function


def _run(awaitable):
    return asyncio.run(awaitable)


async def _async_value(value):
    return value


@pytest.fixture()
def team_api_module(monkeypatch):
    monkeypatch.setattr(api.apps, "login_required", lambda function: function)
    module_path = Path(__file__).resolve().parents[5] / "api" / "apps" / "restful_apis" / "team_api.py"
    spec = importlib.util.spec_from_file_location("test_team_api_unit", module_path)
    module = importlib.util.module_from_spec(spec)
    module.manager = _DummyManager()
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "current_user", SimpleNamespace(id="member-1", email="member@example.com", nickname="Member"))
    return module


def test_create_team_rejects_non_admin(team_api_module, monkeypatch):
    monkeypatch.setattr(team_api_module.UserService, "is_admin", lambda _user_id: False)
    result = _run(team_api_module.create_team())
    assert result["code"] == team_api_module.RetCode.AUTHENTICATION_ERROR
    assert result["message"] == "No authorization."


def test_create_team_uses_current_user_as_actor(team_api_module, monkeypatch):
    seen = []
    monkeypatch.setattr(team_api_module.UserService, "is_admin", lambda _user_id: True)
    monkeypatch.setattr(team_api_module, "_parse_json", lambda _validator: _async_value(({"name": "Finance"}, None)))
    monkeypatch.setattr(
        team_api_module.team_api_service,
        "create_team",
        lambda admin_id, name: seen.append((admin_id, name)) or (True, {"id": "team-1"}),
    )

    result = _run(team_api_module.create_team())

    assert result["code"] == 0
    assert seen == [("member-1", "Finance")]


def test_create_team_returns_standard_validation_error(team_api_module, monkeypatch):
    monkeypatch.setattr(team_api_module.UserService, "is_admin", lambda _user_id: True)
    monkeypatch.setattr(team_api_module, "_parse_json", lambda _validator: _async_value((None, "name: Field required")))

    result = _run(team_api_module.create_team())

    assert result["code"] == team_api_module.RetCode.ARGUMENT_ERROR
    assert "Field required" in result["message"]


def test_accept_invitation_uses_current_user(team_api_module, monkeypatch):
    seen = []
    monkeypatch.setattr(team_api_module, "_parse_json", lambda _validator: _async_value(({"action": "accept"}, None)))
    monkeypatch.setattr(
        team_api_module.team_api_service,
        "update_invitation",
        lambda user_id, team_id, action: seen.append((user_id, team_id, action)) or (True, True),
    )
    result = _run(team_api_module.update_invitation("team-1"))
    assert result["code"] == 0
    assert seen == [("member-1", "team-1", "accept")]


def test_missing_current_user_invitation_returns_authorization_error(team_api_module, monkeypatch):
    monkeypatch.setattr(team_api_module, "_parse_json", lambda _validator: _async_value(({"action": "reject"}, None)))
    monkeypatch.setattr(
        team_api_module.team_api_service,
        "update_invitation",
        lambda *_args: (False, "No authorization."),
    )

    result = _run(team_api_module.update_invitation("hidden-team"))

    assert result["code"] == team_api_module.RetCode.AUTHENTICATION_ERROR
    assert result["message"] == "No authorization."


def test_remove_member_uses_current_user_as_actor(team_api_module, monkeypatch):
    seen = []
    monkeypatch.setattr(
        team_api_module.team_api_service,
        "remove_or_leave_member",
        lambda actor_id, team_id, user_id: seen.append((actor_id, team_id, user_id)) or (True, True),
    )

    result = team_api_module.remove_member("team-1", "member-2")

    assert result["code"] == 0
    assert seen == [("member-1", "team-1", "member-2")]


def test_service_authorization_failures_use_authentication_error(team_api_module, monkeypatch):
    monkeypatch.setattr(team_api_module.team_api_service, "delete_team", lambda *_args: (False, "No authorization."))

    result = team_api_module.delete_team("hidden-team")

    assert result["code"] == team_api_module.RetCode.AUTHENTICATION_ERROR
    assert result["message"] == "No authorization."


def test_invite_schedules_email_after_database_success(team_api_module, monkeypatch):
    sent = []
    created = []
    monkeypatch.setattr(team_api_module.UserService, "is_admin", lambda _user_id: True)
    monkeypatch.setattr(team_api_module, "_parse_json", lambda _validator: _async_value(({"email": "new@example.com"}, None)))
    monkeypatch.setattr(
        team_api_module.team_api_service,
        "invite_member",
        lambda *_args: (True, {"id": "new-user", "email": "new@example.com", "state": "invited"}),
    )
    monkeypatch.setattr(team_api_module.TeamService, "get_owned_team", lambda *_args: SimpleNamespace(name="Finance"))

    async def fake_send(**kwargs):
        sent.append(kwargs)

    class _DoneTask:
        def add_done_callback(self, callback):
            callback(self)

        def result(self):
            return None

    monkeypatch.setattr(team_api_module, "send_team_invite_email", fake_send)
    monkeypatch.setattr(team_api_module.asyncio, "create_task", lambda coroutine: created.append(coroutine) or _DoneTask())

    result = _run(team_api_module.invite_member("team-1"))
    _run(created[0])

    assert result["code"] == 0
    assert sent == [
        {
            "to_email": "new@example.com",
            "invite_url": team_api_module.settings.MAIL_FRONTEND_URL,
            "team_id": "team-1",
            "team_name": "Finance",
            "inviter": "Member",
        }
    ]


def test_invite_failure_does_not_schedule_email(team_api_module, monkeypatch):
    monkeypatch.setattr(team_api_module.UserService, "is_admin", lambda _user_id: True)
    monkeypatch.setattr(team_api_module, "_parse_json", lambda _validator: _async_value(({"email": "new@example.com"}, None)))
    monkeypatch.setattr(team_api_module.team_api_service, "invite_member", lambda *_args: (False, "User not found."))
    monkeypatch.setattr(
        team_api_module.asyncio,
        "create_task",
        lambda _coroutine: pytest.fail("email must not be scheduled before a successful database write"),
    )

    result = _run(team_api_module.invite_member("team-1"))

    assert result["code"] == team_api_module.RetCode.DATA_ERROR
