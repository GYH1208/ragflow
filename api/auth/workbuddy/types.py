"""Normalized employee identity; credentials never become persisted profile fields."""

from dataclasses import dataclass


class WorkBuddyAuthenticationError(Exception):
    pass


class WorkBuddyProviderUnavailable(Exception):
    pass


@dataclass(frozen=True)
class DepartmentNode:
    id: str
    name: str
    parent_id: str | None


@dataclass(frozen=True)
class DepartmentPath:
    department_id: str
    is_main: bool
    complete: bool
    hierarchy: tuple[DepartmentNode, ...]


@dataclass(frozen=True)
class VerifiedEmployee:
    enterprise_id: str
    union_id: str
    issuer: str
    application_id: str
    alias_id: str | None
    name: str
    directory_status: str
    paths: tuple[DepartmentPath, ...]


@dataclass(frozen=True)
class WorkBuddyAccessContext:
    actor_user_id: str
    enterprise_id: str
    center_id: str | None
    allowed_dataset_ids: frozenset[str]
    scope: str = "knowledge:retrieve"
