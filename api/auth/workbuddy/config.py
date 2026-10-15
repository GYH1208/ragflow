"""Server configuration for one explicitly trusted enterprise/application."""

import os
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorkBuddyConfig:
    enabled: bool = False
    issuer: str = ""
    enterprise_id: str = ""
    application_id: str = ""
    client_secret: str = field(default="", repr=False)
    requesting_clients: frozenset[str] = frozenset({"codebuddy"})

    def __post_init__(self):
        if self.enabled and (
            not re.fullmatch(r"[0-9]{1,20}", self.enterprise_id)
            or self.issuer != "https://oauth2.account.tencent.com/authz/oidc/v2/" + self.enterprise_id
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.application_id)
            or not self.client_secret
            or not self.requesting_clients
            or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", c) for c in self.requesting_clients)
        ):
            raise ValueError("Invalid or missing WorkBuddy server configuration")

    @classmethod
    def from_env(cls):
        enabled = os.getenv("RAGFLOW_WORKBUDDY_ENABLED", "false").lower() == "true"
        if not enabled:
            return cls()
        return cls(
            True,
            os.getenv("RAGFLOW_WORKBUDDY_ISSUER", ""),
            os.getenv("RAGFLOW_WORKBUDDY_TENANT_ID", ""),
            os.getenv("RAGFLOW_WORKBUDDY_APPLICATION_ID", ""),
            os.getenv("RAGFLOW_WORKBUDDY_CLIENT_SECRET", ""),
            frozenset(x.strip() for x in os.getenv("RAGFLOW_WORKBUDDY_REQUESTING_CLIENTS", "codebuddy").split(",") if x.strip()),
        )
