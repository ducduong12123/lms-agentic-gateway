"""Closed contracts returned by mutating tools and client-directed tools."""
from __future__ import annotations

import re
import time
import uuid
from urllib.parse import unquote, urlsplit

ROUTE_PATTERNS = (
    re.compile(r"^/lms/?$"),
    re.compile(r"^/lms/courses/[^/]+/?$"),
    re.compile(r"^/lms/courses/[^/]+/learn/\d+-\d+/?$"),
    re.compile(r"^/lms/batches(?:/[^/]+)?/?$"),
)


def validate_route(route: str) -> str:
    value = str(route or "").strip()
    if not value or value.startswith(("//", "http:", "https:", "javascript:", "data:")):
        raise ValueError("route must be a local LMS path")
    parsed = urlsplit(value)
    path = unquote(parsed.path)
    if parsed.scheme or parsed.netloc or ".." in path:
        raise ValueError("route must be a safe local LMS path")
    if not any(pattern.fullmatch(path) for pattern in ROUTE_PATTERNS):
        raise ValueError("route is outside the LMS navigation whitelist")
    return value


VIEW_FIELDS: dict[str, set[str]] = {
    "review_set": {"set_id"},
    "session": {"mode", "concept_id"},
    "knowledge_map": {"course"},
    "mastery_detail": {"concept_id"},
    "plan": {"date"},
    "diff": {"doctype", "name", "before", "after"},
    "course": {"course"},
    "lesson": {"course", "chapter", "lesson_number"},
}


def validate_view_spec(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("view spec must be an object")
    kind = str(spec.get("type") or "")
    fields = VIEW_FIELDS.get(kind)
    if fields is None:
        raise ValueError(f"unsupported view type: {kind}")
    missing = sorted(field for field in fields if field not in spec)
    if missing:
        raise ValueError(f"view type {kind} missing fields: {', '.join(missing)}")
    return {"type": kind, **{field: spec[field] for field in fields}}


def client_directive(op: str, **kwargs) -> dict:
    if op == "navigate":
        route = validate_route(str(kwargs.get("route") or ""))
        return {"kind": "client", "op": op, "route": route}
    if op == "render_view":
        return {"kind": "client", "op": op, "spec": validate_view_spec(dict(kwargs.get("spec") or {}))}
    raise ValueError(f"unsupported client operation: {op}")


def reversibility_contract_label(reversibility: str) -> str:
    return {
        "reversible": "Lùi được",
        "compensating": "Bù trừ được",
        "irreversible": "Không lùi được",
    }.get(str(reversibility or ""), "Không lùi được")


def action_result(
    action: str,
    title: str,
    summary: str,
    changes: list[dict] | None = None,
    undo: dict | None = None,
    view: dict | None = None,
    status: str = "done",
    plan_id: str | None = None,
    items: list[dict] | None = None,
    requires_edit_review: bool = False,
    preview: str | None = None,
    reversibility: str | None = None,
    typed_confirm: str | None = None,
) -> dict:
    if status not in {"done", "pending_approval", "noop", "failed"}:
        raise ValueError(f"unsupported action status: {status}")
    action_id = "act_" + uuid.uuid4().hex[:8]
    normalized_undo = None
    if undo and status == "done":
        normalized_undo = {**undo, "available": True, "expires_at": undo.get("expires_at", time.time() + 600)}
    normalized_view = validate_view_spec(view) if view else None
    result = {
        "kind": "action",
        "action": str(action),
        "action_id": action_id,
        "status": status,
        "title": str(title),
        "summary": str(summary),
        "changes": list(changes or []),
        "undo": normalized_undo or {"available": False, "expires_at": None},
        "view": normalized_view,
    }
    if status == "pending_approval":
        normalized_reversibility = str(reversibility or "")
        if normalized_reversibility not in {"reversible", "compensating", "irreversible"}:
            normalized_reversibility = "irreversible" if not normalized_undo else "reversible"
        normalized_items = []
        for index, raw_item in enumerate(list(items or [])):
            item = dict(raw_item or {})
            item.setdefault("id", f"item_{index + 1}")
            item.setdefault("status", "pending")
            normalized_items.append(item)
        result.update({
            "plan_id": str(plan_id or ""),
            "items": normalized_items,
            "requires_edit_review": bool(requires_edit_review),
            "preview": str(preview or summary or ""),
            "reversibility": normalized_reversibility,
            "reversibility_label": reversibility_contract_label(normalized_reversibility),
        })
        if typed_confirm:
            result["typed_confirm"] = str(typed_confirm)
    return result
