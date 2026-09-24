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

from datetime import UTC, date, datetime, time, timedelta

from quart import request

from api.apps import current_user, login_required
from api.db.services.chat_analytics_service import ChatAnalyticsService
from api.db.services.user_service import UserTenantService
from api.utils.api_utils import (
    get_data_error_result,
    get_json_result,
    server_error_response,
)


def _parse_date(value: str | None, default) -> datetime:
    selected = date.fromisoformat(value) if value else default
    return datetime.combine(selected, time.min, tzinfo=UTC)


@manager.route("/chat-analytics", methods=["GET"])  # noqa: F821
@login_required
async def chat_analytics():
    try:
        now = datetime.now(UTC)
        today = now.date()
        granularity = request.args.get("granularity", "day")
        if granularity not in {"day", "week", "month"}:
            raise ValueError("Granularity must be day, week, or month")

        from_date = _parse_date(
            request.args.get("from_date"), today - timedelta(days=29)
        )
        to_date = _parse_date(request.args.get("to_date"), today).replace(
            hour=23, minute=59, second=59
        )
        if from_date > to_date:
            raise ValueError("Start date must not be after end date")

        tenants = UserTenantService.query(user_id=current_user.id)
        if not tenants:
            return get_data_error_result(message="Tenant not found")

        result = ChatAnalyticsService.dashboard(
            tenant_id=tenants[0].tenant_id,
            dialog_id=request.args.get("dialog_id") or None,
            from_date=from_date,
            to_date=to_date,
            granularity=granularity,
            now=now,
        )
        return get_json_result(data=result)
    except ValueError as error:
        return get_data_error_result(message=str(error))
    except Exception as error:  # noqa: BLE001
        return server_error_response(error)
