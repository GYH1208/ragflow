"""Bounded identity caches backed by online checks against pinned OneID URLs."""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections import OrderedDict

import httpx

from .types import DepartmentNode, DepartmentPath, VerifiedEmployee, WorkBuddyAuthenticationError, WorkBuddyProviderUnavailable

CONTACTS = "https://api.account.tencent.com/openapi/v3/contacts/"
logger = logging.getLogger(__name__)


def _numeric(value):
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,20}", value) or int(value) <= 0:
        raise ValueError("Invalid department identifier")
    return value


class OneIDClient:
    CACHE_MAX_ENTRIES = 1024

    def __init__(self, config, transport=None):
        self.config = config
        self._client = httpx.AsyncClient(timeout=5, follow_redirects=False, transport=transport)
        self._metadata_until = 0
        self._app_token = ""
        self._app_token_until = 0
        self._lock = asyncio.Lock()
        self._tickets = OrderedDict()
        self._profiles = OrderedDict()
        # Fixed lock stripes bound memory even for a flood of distinct credentials.
        # Separate lock sets avoid a token/profile hash collision deadlocking.
        self._ticket_locks = [asyncio.Lock() for _ in range(256)]
        self._profile_locks = [asyncio.Lock() for _ in range(256)]
        # Opt-in, absolute deadline: restarting the service does not re-arm it.
        try:
            self._timing_until = int(os.getenv("RAGFLOW_WORKBUDDY_TOKEN_TIMING_UNTIL", "0"))
            now = time.time()
            if not now < self._timing_until <= now + 86400:
                self._timing_until = 0
        except ValueError:
            self._timing_until = 0
        self._timing_count = 0
        if self._timing_until > time.time():
            try:
                logger.info("workbuddy_token_timing_enabled until=%d sample_limit=100", self._timing_until)
            except Exception:  # noqa: BLE001, S110 -- logging itself may be unavailable
                pass  # Optional diagnostics must not prevent client startup.

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        self._tickets.clear()
        self._profiles.clear()
        await self._client.aclose()

    @staticmethod
    def _cached(cache, key):
        entry = cache.get(key)
        if entry is None:
            return None
        deadline, value = entry
        if time.monotonic() >= deadline:
            cache.pop(key, None)
            return None
        cache.move_to_end(key)
        return value

    def _remember(self, cache, key, deadline, value):
        if deadline <= time.monotonic():
            return
        cache[key] = (deadline, value)
        cache.move_to_end(key)
        while len(cache) > self.CACHE_MAX_ENTRIES:
            cache.popitem(last=False)

    def _observe_verified_times(self, info):
        now = time.time()
        if now >= self._timing_until or self._timing_count >= 100:
            return
        now = int(now)
        self._timing_count += 1
        issued = info.get("iat")
        # Explicit numeric allowlist; never log credentials, identity or raw claims.
        logger.info(
            "workbuddy_token_timing %s",
            json.dumps(
                {
                    "observed_at": int(now),
                    "iat": issued,
                    "exp": info["exp"],
                    "remaining_seconds": max(0, info["exp"] - now),
                    "lifetime_seconds": info["exp"] - issued if issued is not None else None,
                }
            ),
        )

    async def _json(self, url, *, token=None, form=None):
        try:
            async with self._client.stream("POST" if form is not None else "GET", url, headers={"Authorization": "Bearer " + token} if token else {}, data=form) as res:
                if res.status_code != 200:
                    raise WorkBuddyProviderUnavailable("OneID request failed")
                body = bytearray()
                async for part in res.aiter_bytes():
                    body.extend(part)
                    if len(body) > 262144:
                        raise WorkBuddyProviderUnavailable("OneID response exceeded limit")
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise WorkBuddyProviderUnavailable("Invalid OneID response")
                return data
        except (httpx.HTTPError, ValueError):
            raise WorkBuddyProviderUnavailable("OneID service unavailable") from None

    async def _metadata(self):
        async with self._lock:
            if time.monotonic() < self._metadata_until:
                return
            issuer = self.config.issuer
            data = await self._json(issuer + "/.well-known/openid-configuration")
            if (
                data.get("issuer") != issuer
                or data.get("introspection_endpoint") != issuer + "/introspect"
                or data.get("userinfo_endpoint") != issuer + "/userinfo"
                or "client_secret_post" not in data.get("introspection_endpoint_auth_methods_supported", [])
            ):
                raise WorkBuddyProviderUnavailable("Untrusted OneID metadata")
            self._metadata_until = time.monotonic() + 600

    async def _application_token(self):
        async with self._lock:
            if time.monotonic() < self._app_token_until:
                return self._app_token
            data = await self._json(self.config.issuer + "/token", form={"grant_type": "client_credentials", "client_id": self.config.application_id, "client_secret": self.config.client_secret})
            access, expires = data.get("access_token"), data.get("expires_in")
            if not isinstance(access, str) or not access or type(expires) is not int or expires <= 0:
                raise WorkBuddyProviderUnavailable("OneID application credential unavailable")
            self._app_token = access
            self._app_token_until = time.monotonic() + max(0, min(expires - 30, 300))
            return access

    async def _path(self, member, token, cache):
        nodes = []
        seen = set()
        complete = False
        try:
            leaf = _numeric(member.get("department_id"))
            current = leaf
            for _ in range(24):
                if current in seen:
                    break
                seen.add(current)
                if current not in cache:
                    response = await self._json(CONTACTS + "departments/" + current, token=token)
                    node = response.get("data", {}).get("department", {})
                    if type(response.get("code")) is not int or response["code"] != 0 or _numeric(node.get("department_id")) != current:
                        raise ValueError("Invalid department")
                    cache[current] = node
                node = cache[current]
                name = node.get("department_name")
                if not isinstance(name, str) or not 0 < len(name) <= 256:
                    raise ValueError("Invalid department name")
                parent = node.get("department_parent_id")
                root = parent is None or parent in ("", "0") or type(parent) is int and parent == 0
                parent_id = None if root else _numeric(parent)
                nodes.append(DepartmentNode(current, name, parent_id))
                if "department_parent_id" not in node:
                    if "_roots" not in cache:
                        roots = await self._json(CONTACTS + "departments/roots", token=token)
                        values = roots.get("data", {}).get("roots")
                        if type(roots.get("code")) is not int or roots["code"] != 0 or not isinstance(values, list):
                            raise ValueError("Invalid roots")
                        cache["_roots"] = {_numeric(x.get("department_id")) for x in values}
                    complete = current in cache["_roots"]
                    break
                if root:
                    complete = True
                    break
                current = parent_id
        except (ValueError, TypeError, AttributeError, WorkBuddyProviderUnavailable):
            leaf = str(member.get("department_id", ""))[:64]
        return DepartmentPath(leaf, member.get("is_main_department") is True, complete, tuple(reversed(nodes)))

    async def _verify_ticket(self, token):
        await self._metadata()
        conf = self.config
        info = await self._json(conf.issuer + "/introspect", form={"token": token, "token_type_hint": "access_token", "client_id": conf.application_id, "client_secret": conf.client_secret})
        now = time.time()
        if (
            info.get("active") is not True
            or info.get("client_id") not in conf.requesting_clients
            or info.get("aud") not in (conf.application_id, [conf.application_id])
            or info.get("iss") != conf.issuer
            or info.get("tid") != conf.enterprise_id
            or type(info.get("exp")) is not int
            or info["exp"] <= now
            or any(type(info[k]) is not int or info[k] > now for k in ("iat", "nbf") if k in info)
            or str(info.get("token_type", "Bearer")).lower() != "bearer"
        ):
            raise WorkBuddyAuthenticationError("Employee credential validation failed")
        subject = info.get("sub")
        if not isinstance(subject, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", subject) or subject in conf.requesting_clients or subject == conf.application_id:
            raise WorkBuddyAuthenticationError("Employee subject required")
        profile = await self._json(conf.issuer + "/userinfo", token=token)
        tids = [profile[k] for k in ("tid", "account_id") if k in profile]
        if profile.get("sub") != subject or not tids or any(t != conf.enterprise_id for t in tids):
            raise WorkBuddyAuthenticationError("Employee profile mismatch")
        # Store only the verified identity and bounded fallback display fields.
        fallback = {key: profile.get(key) for key in ("name", "alias_id") if isinstance(profile.get(key), str)}
        return {"sub": subject, "exp": info["exp"], "iat": info.get("iat"), "profile": fallback}

    async def _directory_profile(self, ticket):
        conf = self.config
        subject, profile = ticket["sub"], ticket["profile"]
        app = await self._application_token()
        result = await self._json(CONTACTS + "users/" + subject + "?ignore_status=false", token=app)
        user = result.get("data", {}).get("user", {})
        if type(result.get("code")) is not int or result["code"] != 0 or user.get("union_id") != subject or user.get("status") not in ("Enable", "Enabled"):
            raise WorkBuddyAuthenticationError("Employee directory account is not enabled")
        members = user.get("departments", [])
        if not isinstance(members, list) or len(members) > 16 or any(not isinstance(m, dict) for m in members):
            members = []
        cache = {}
        paths = tuple([await self._path(m, app, cache) for m in members])
        name = user.get("name") or profile.get("name") or subject
        alias = user.get("alias_id") or profile.get("alias_id")
        if not isinstance(name, str) or not 0 < len(name) <= 100 or alias is not None and (not isinstance(alias, str) or len(alias) > 128):
            raise WorkBuddyAuthenticationError("Invalid employee profile")
        return VerifiedEmployee(conf.enterprise_id, subject, conf.issuer, conf.application_id, alias, name, user["status"], paths)

    async def _employee_profile(self, ticket):
        key = (self.config.enterprise_id, ticket["sub"])
        async with self._profile_locks[hash(key) % len(self._profile_locks)]:
            employee = self._cached(self._profiles, key)
            if employee is None:
                deadline = time.monotonic() + self.config.profile_cache_ttl_seconds
                employee = await self._directory_profile(ticket)
                # Incomplete lookups are retried on the next request, not frozen for a day.
                if employee.paths and all(path.complete for path in employee.paths) and sum(path.is_main for path in employee.paths) == 1:
                    self._remember(self._profiles, key, deadline, employee)
            return employee

    async def verify_employee(self, token):
        if not self.config.enabled or not isinstance(token, str) or not token or len(token) > 32768 or "," in token or any(c.isspace() for c in token):
            raise WorkBuddyAuthenticationError("Invalid employee credential")
        # Original credentials never become cache keys or cache values.
        key = hashlib.sha256(token.encode()).digest()
        async with self._ticket_locks[key[0]]:
            try:
                ticket = self._cached(self._tickets, key)
                if ticket is not None and ticket["exp"] <= time.time():
                    self._tickets.pop(key, None)
                    ticket = None
                fresh = ticket is None
                if not fresh:
                    deadline = self._tickets[key][0]
                if fresh:
                    started = time.monotonic()
                    ticket = await self._verify_ticket(token)
                    # Compare before subtraction to avoid converting huge integer exp to float.
                    ttl = self.config.token_cache_ttl_seconds
                    wall = time.time()
                    remaining = ttl if ticket["exp"] >= wall + ttl else max(0, ticket["exp"] - wall)
                    deadline = min(started + ttl, time.monotonic() + remaining)
                employee = await self._employee_profile(ticket)
                # Recheck after any queued/network work; no stale credential may be returned.
                if ticket["exp"] <= time.time() or (self.config.token_cache_ttl_seconds > 0 and time.monotonic() >= deadline):
                    self._tickets.pop(key, None)
                    raise WorkBuddyAuthenticationError("Employee credential expired")
                if fresh:
                    self._remember(self._tickets, key, deadline, ticket)
                    try:
                        self._observe_verified_times(ticket)
                    except Exception:  # noqa: BLE001, S110 -- diagnostics do not decide authentication
                        pass
                return employee
            except (KeyError, TypeError, AttributeError, ValueError):
                raise WorkBuddyAuthenticationError("Invalid OneID identity response") from None
