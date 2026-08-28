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
import logging

from quart import request

from api.apps import current_user, login_required
from api.apps.services import team_api_service
from api.db.services.user_service import UserService
from api.utils.api_utils import get_data_error_result, get_error_argument_result, get_json_result
from api.utils.validation_utils import (
    CreateTeamReq,
    InviteTeamMemberReq,
    UpdateTeamInvitationReq,
    UpdateTeamReq,
    validate_and_parse_json_request,
)
from api.utils.web_utils import send_team_invite_email
from common import settings
from common.constants import RetCode

logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task] = set()


async def _parse_json(validator):
    return await validate_and_parse_json_request(request, validator)


def _service_response(success, result):
    if success:
        return get_json_result(data=result)
    if result == team_api_service.NO_AUTHORIZATION:
        return get_json_result(data=False, message=result, code=RetCode.AUTHENTICATION_ERROR)
    return get_data_error_result(message=result)


def _no_authorization():
    return get_json_result(data=False, message=team_api_service.NO_AUTHORIZATION, code=RetCode.AUTHENTICATION_ERROR)


def _track_background_task(task, *, team_id, to_email):
    if not isinstance(task, asyncio.Task):
        return
    _background_tasks.add(task)

    def _on_done(done_task):
        _background_tasks.discard(done_task)
        try:
            done_task.result()
        except asyncio.CancelledError:
            logger.warning("Team invitation email task cancelled: team_id=%s to=%s", team_id, to_email)
        except Exception:
            logger.exception("Team invitation email task failed: team_id=%s to=%s", team_id, to_email)

    task.add_done_callback(_on_done)


@manager.route("/teams", methods=["GET"])  # noqa: F821
@login_required
def list_teams():
    return _service_response(*team_api_service.list_teams(current_user.id))


@manager.route("/teams", methods=["POST"])  # noqa: F821
@login_required
async def create_team():
    if not UserService.is_admin(current_user.id):
        return _no_authorization()
    req, err = await _parse_json(CreateTeamReq)
    if err is not None:
        return get_error_argument_result(err)
    return _service_response(*team_api_service.create_team(current_user.id, req["name"]))


@manager.route("/teams/<team_id>", methods=["GET"])  # noqa: F821
@login_required
def get_team(team_id):
    return _service_response(*team_api_service.get_team(current_user.id, team_id))


@manager.route("/teams/<team_id>", methods=["PUT", "PATCH"])  # noqa: F821
@login_required
async def rename_team(team_id):
    req, err = await _parse_json(UpdateTeamReq)
    if err is not None:
        return get_error_argument_result(err)
    return _service_response(*team_api_service.rename_team(current_user.id, team_id, req["name"]))


@manager.route("/teams/<team_id>", methods=["DELETE"])  # noqa: F821
@login_required
def delete_team(team_id):
    return _service_response(*team_api_service.delete_team(current_user.id, team_id))


@manager.route("/teams/<team_id>/members", methods=["GET"])  # noqa: F821
@login_required
def list_members(team_id):
    return _service_response(*team_api_service.list_members(current_user.id, team_id))


@manager.route("/teams/<team_id>/members", methods=["POST"])  # noqa: F821
@login_required
async def invite_member(team_id):
    if not UserService.is_admin(current_user.id):
        return _no_authorization()
    req, err = await _parse_json(InviteTeamMemberReq)
    if err is not None:
        return get_error_argument_result(err)
    success, result = team_api_service.invite_member(current_user.id, team_id, req["email"])
    if not success:
        return _service_response(success, result)

    result = dict(result)
    team_name = result.pop("_team_name")
    task = asyncio.create_task(
        send_team_invite_email(
            to_email=req["email"],
            invite_url=settings.MAIL_FRONTEND_URL,
            team_id=team_id,
            team_name=team_name,
            inviter=current_user.nickname or current_user.email,
        )
    )
    _track_background_task(task, team_id=team_id, to_email=req["email"])
    return get_json_result(data=result)


@manager.route("/teams/<team_id>/members/<user_id>", methods=["DELETE"])  # noqa: F821
@login_required
def remove_member(team_id, user_id):
    return _service_response(*team_api_service.remove_or_leave_member(current_user.id, team_id, user_id))


@manager.route("/teams/<team_id>/invitation", methods=["PATCH"])  # noqa: F821
@login_required
async def update_invitation(team_id):
    req, err = await _parse_json(UpdateTeamInvitationReq)
    if err is not None:
        return get_error_argument_result(err)
    return _service_response(*team_api_service.update_invitation(current_user.id, team_id, req["action"]))
