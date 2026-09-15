"""Resolve a trusted LMS identity from Frappe's HttpOnly ``sid`` cookie."""
from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass

from gateway.connector.frappe_client import FrappeClient


@dataclass(frozen=True)
class Identity:
    user: str
    role: str
    roles: tuple[str, ...]
    sid: str


def map_role(roles: list[str] | tuple[str, ...]) -> str:
    role_set = set(roles)
    if role_set.intersection({"System Manager", "Moderator"}):
        return "admin"
    if "Course Creator" in role_set:
        return "teacher"
    if "Batch Evaluator" in role_set:
        return "evaluator"
    return "student"


class IdentityResolver:
    def __init__(self, frappe: FrappeClient, ttl_seconds: int = 60):
        self.frappe = frappe
        self.ttl_seconds = max(5, int(ttl_seconds))
        self._cache: dict[str, tuple[float, Identity]] = {}
        self._lock = threading.RLock()

    def resolve(self, sid: str) -> Identity:
        if not sid or sid == "Guest":
            raise PermissionError("missing Frappe session")
        cache_key = hashlib.sha256(sid.encode("utf-8")).hexdigest()
        now = time.time()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and cached[0] > now:
                return cached[1]

        client = self.frappe.with_session(sid)
        logged_in = client.get_method("frappe.auth.get_logged_user").get("message")
        if not logged_in or logged_in == "Guest":
            raise PermissionError("invalid Frappe session")
        info = client.get_method("lms.lms.api.get_user_info").get("message") or {}
        if info.get("name") and info.get("name") != logged_in:
            raise PermissionError("Frappe identity mismatch")
        roles = tuple(str(role) for role in (info.get("roles") or []))
        identity = Identity(str(logged_in), map_role(roles), roles, sid)
        with self._lock:
            self._cache[cache_key] = (now + self.ttl_seconds, identity)
        return identity
