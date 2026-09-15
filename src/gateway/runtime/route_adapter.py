"""Contract adapter for the public Frappe LMS SPA routes."""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

LESSON_ROUTE = re.compile(
    r"^/lms/courses/(?P<course>[^/]+)/learn/(?P<chapter>\d+)-(?P<lesson>\d+)/?$"
)
COURSE_ROUTE = re.compile(r"^/lms/courses/(?P<course>[^/]+)/?(?:learn)?/?$")


def parse_lms_route(value: str) -> dict[str, str | int]:
    path = urlsplit(str(value or "")).path.rstrip("/") or "/"
    match = LESSON_ROUTE.match(path)
    if match:
        return {
            "kind": "lesson",
            "course": unquote(match.group("course")),
            "chapter": int(match.group("chapter")),
            "lesson_number": int(match.group("lesson")),
            "path": path,
        }
    match = COURSE_ROUTE.match(path)
    if match:
        return {"kind": "course", "course": unquote(match.group("course")), "path": path}
    return {"kind": "other", "path": path}


def resolve_lms_context(frappe, value: str) -> dict:
    """Resolve ordinal lesson routes through Frappe's permission-aware API."""
    route = parse_lms_route(value)
    if route["kind"] != "lesson":
        return route
    try:
        response = frappe.get_method(
            "lms.lms.utils.get_lesson",
            course=route["course"],
            chapter=route["chapter"],
            lesson=route["lesson_number"],
        )
        doc = response.get("message") or response.get("data") or {}
        if isinstance(doc, dict) and doc.get("name") and not doc.get("locked"):
            route["lesson"] = str(doc["name"])
            route["lesson_title"] = str(doc.get("title") or "")
            route["course"] = str(doc.get("course") or route["course"])
    except Exception:
        route["unresolved"] = True
    return route


def load_lesson_context(frappe, route: dict, role: str = "student") -> dict:
    """Read the current lesson through the permission-aware method used by the SPA."""
    if not isinstance(route, dict) or route.get("kind") != "lesson":
        return {}
    try:
        response = frappe.get_method(
            "lms.lms.utils.get_lesson",
            course=route.get("course", ""), chapter=route.get("chapter", 0),
            lesson=route.get("lesson_number", 0),
        )
        doc = response.get("message") or response.get("data") or {}
        if not isinstance(doc, dict) or doc.get("locked") or not doc.get("name"):
            return {}
        if role == "student":
            allowed = {"name", "title", "body", "content", "quiz_id", "course", "chapter", "include_in_preview"}
            doc = {key: value for key, value in doc.items() if key in allowed}
        for key in ("body", "content"):
            if doc.get(key):
                doc[key] = str(doc[key])[:12000]
        return doc
    except Exception:
        return {}
