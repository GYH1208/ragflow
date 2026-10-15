"""Per-request external authentication and current database authorization."""

import asyncio
import json
import logging
from uuid import uuid4

from quart import current_app, g
from werkzeug.exceptions import Forbidden

from api.auth.workbuddy.config import WorkBuddyConfig
from api.auth.workbuddy.oneid_client import OneIDClient
from api.auth.workbuddy.types import WorkBuddyAccessContext, WorkBuddyAuthenticationError
from api.db.db_models import User
from api.db.services.center_dataset_service import CenterDatasetService as Centers
from api.db.services.workbuddy_employee_service import WorkBuddyEmployeeService as Employees

logger = logging.getLogger(__name__)


def _sync_context(profile):
    database = User._meta.database
    with database.connection_context():
        Centers.sync_departments(profile.enterprise_id, profile.paths)
        user = Employees.sync_employee(profile)
        user = User.get_by_id(user.id)
        if user.is_active != "1" or user.status != "1":
            raise Forbidden("Employee account is disabled")
        center = Centers.resolve_center(profile.enterprise_id, profile.paths)
        context = WorkBuddyAccessContext(user.id, profile.enterprise_id, center, Centers.allowed_datasets(profile.enterprise_id, center))
        return user, context


async def authenticate_workbuddy(token):
    client = current_app.extensions.get("workbuddy_oneid")
    if client is None:
        config = WorkBuddyConfig.from_env()
        if not config.enabled:
            raise WorkBuddyAuthenticationError("WorkBuddy authentication is disabled")
        client = OneIDClient(config)
        current_app.extensions["workbuddy_oneid"] = client
    profile = await client.verify_employee(token)
    return await asyncio.to_thread(_sync_context, profile)


def extract_oneid_token(headers):
    values = headers.getlist("x-oneid-access-token")
    if len(values) != 1 or not values[0] or len(values[0]) > 32768 or "," in values[0] or any(c.isspace() for c in values[0]):
        raise WorkBuddyAuthenticationError("Invalid employee credential header")
    return values[0]


def install_workbuddy_lifecycle(app):
    @app.before_serving
    async def start_workbuddy():
        config = WorkBuddyConfig.from_env()
        if config.enabled:
            app.extensions["workbuddy_oneid"] = OneIDClient(config)

    @app.after_serving
    async def close_workbuddy():
        client = app.extensions.pop("workbuddy_oneid", None)
        if client:
            await client.close()

    @app.after_request
    async def audit_workbuddy(response):
        context = getattr(g, "workbuddy_context", None)
        if context is not None:
            logger.info(
                "workbuddy_access %s",
                json.dumps(
                    {
                        "request_id": uuid4().hex,
                        "actor_user_id": context.actor_user_id,
                        "enterprise_id": context.enterprise_id,
                        "center_id": context.center_id,
                        "scope": context.scope,
                        "dataset_ids": getattr(g, "workbuddy_checked_dataset_ids", []),
                        "result": response.status_code,
                    }
                ),
            )
        return response
