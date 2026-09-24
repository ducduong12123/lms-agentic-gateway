"""Durable course-authoring workspaces for long-running widget sessions."""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from sqlite3 import Connection, Row
from typing import Any

from . import learner

_STATE_FIELDS = {
    "brief",
    "audience",
    "learning_outcomes",
    "constraints",
    "module_plan",
    "decisions",
    "completed_items",
    "next_actions",
    "current_focus",
}
_LIST_FIELDS = {
    "learning_outcomes",
    "constraints",
    "module_plan",
    "decisions",
    "completed_items",
    "next_actions",
}
_STATUSES = {"planning", "authoring", "review", "completed", "archived"}


def _migrate(path: Path | None = None) -> None:
    with learner._conn(path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS course_project ("
            "id TEXT PRIMARY KEY, member TEXT NOT NULL, title TEXT NOT NULL, "
            "course TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'planning', "
            "state_json TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL, updated REAL NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_course_project_member "
            "ON course_project(member,updated DESC)"
        )


def _decode(raw: str) -> dict[str, Any]:
    try:
        value: Any = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        value = {}
    return dict(value) if isinstance(value, dict) else {}


def _clean_patch(patch: Mapping[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in dict(patch or {}).items():
        key = str(key)
        if key not in _STATE_FIELDS or value is None:
            continue
        if key in _LIST_FIELDS:
            if not isinstance(value, list):
                raise ValueError(f"{key} phải là danh sách.")
            result[key] = value[:100]
        else:
            result[key] = str(value).strip()[:12000]
    encoded = json.dumps(result, ensure_ascii=False, default=str)
    if len(encoded) > 100_000:
        raise ValueError("checkpoint dự án vượt quá 100000 ký tự.")
    return result


def _row(row: Row) -> dict[str, Any]:
    result: dict[str, Any] = dict(row)
    result["state"] = _decode(str(result.pop("state_json", "{}")))
    return result


def _assert_session(conn: Connection, member: str, session_id: str) -> None:
    owner = conn.execute(
        "SELECT 1 FROM chat_session WHERE id=? AND member=?", (session_id, member),
    ).fetchone()
    if not owner:
        raise ValueError("session không thuộc user.")


def _activate(
    conn: Connection, member: str, session_id: str, project_id: str, now: float,
) -> None:
    row = conn.execute(
        "SELECT state_json FROM conversation_state WHERE session_id=? AND member=?",
        (session_id, member),
    ).fetchone()
    state = _decode(str(row["state_json"] or "{}")) if row else {}
    state["active_project_id"] = project_id
    conn.execute(
        "INSERT INTO conversation_state(session_id,member,state_json,updated) VALUES(?,?,?,?) "
        "ON CONFLICT(session_id) DO UPDATE SET member=excluded.member,"
        "state_json=excluded.state_json,updated=excluded.updated",
        (session_id, member, json.dumps(state, ensure_ascii=False), now),
    )


def get_project(
    member: str, project_id: str, path: Path | None = None,
) -> dict[str, Any] | None:
    _migrate(path)
    with learner._conn(path) as conn:
        row = conn.execute(
            "SELECT * FROM course_project WHERE id=? AND member=?",
            (str(project_id or ""), str(member or "")),
        ).fetchone()
    return _row(row) if row else None


def active_project(
    member: str, session_id: str, path: Path | None = None,
) -> dict[str, Any] | None:
    _migrate(path)
    with learner._conn(path) as conn:
        row = conn.execute(
            "SELECT state_json FROM conversation_state WHERE session_id=? AND member=?",
            (str(session_id or ""), str(member or "")),
        ).fetchone()
    state = _decode(str(row["state_json"] or "{}")) if row else {}
    return get_project(member, str(state.get("active_project_id") or ""), path)


def list_projects(
    member: str, limit: int = 20, path: Path | None = None,
) -> list[dict[str, Any]]:
    _migrate(path)
    limit = max(1, min(100, int(limit or 20)))
    with learner._conn(path) as conn:
        rows = conn.execute(
            "SELECT * FROM course_project WHERE member=? ORDER BY updated DESC LIMIT ?",
            (str(member or ""), limit),
        ).fetchall()
    return [_row(row) for row in rows]


def checkpoint_project(
    member: str,
    session_id: str,
    *,
    project_id: str = "",
    title: str = "",
    course: str | None = None,
    status: str | None = None,
    patch: Mapping[str, Any] | None = None,
    new_project: bool = False,
    path: Path | None = None,
) -> dict[str, Any]:
    """Create, activate, or partially update the canonical authoring checkpoint."""
    _migrate(path)
    member = str(member or "").strip()
    session_id = str(session_id or "").strip()
    if not member or member == "Guest" or not session_id:
        raise ValueError("thiếu member hoặc session.")
    now = time.time()
    clean = _clean_patch(patch)
    if status and status not in _STATUSES:
        raise ValueError("status dự án không hợp lệ.")
    with learner._conn(path) as conn:
        _assert_session(conn, member, session_id)
        row = None
        if project_id and not new_project:
            row = conn.execute(
                "SELECT * FROM course_project WHERE id=? AND member=?", (project_id, member),
            ).fetchone()
            if not row:
                raise ValueError("không tìm thấy dự án khóa học của user.")
        if not row and not new_project:
            active = active_project(member, session_id, path)
            if active:
                row = conn.execute(
                    "SELECT * FROM course_project WHERE id=? AND member=?",
                    (active["id"], member),
                ).fetchone()
        if not row:
            clean_title = str(title or "").strip()[:180]
            if not clean_title:
                raise ValueError("title là bắt buộc khi tạo dự án khóa học.")
            project_id = "cprj_" + uuid.uuid4().hex[:12]
            initial = {
                "brief": "",
                "audience": "",
                "learning_outcomes": [],
                "constraints": [],
                "module_plan": [],
                "decisions": [],
                "completed_items": [],
                "next_actions": [],
                "current_focus": "",
                **clean,
            }
            conn.execute(
                "INSERT INTO course_project(id,member,title,course,status,state_json,created,updated) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (project_id, member, clean_title, str(course or ""), status or "planning",
                 json.dumps(initial, ensure_ascii=False), now, now),
            )
        else:
            existing = _row(row)
            project_id = str(existing["id"])
            state = {**existing["state"], **clean}
            next_status = str(status or existing["status"])
            if next_status not in _STATUSES:
                raise ValueError("status dự án không hợp lệ.")
            conn.execute(
                "UPDATE course_project SET title=?,course=?,status=?,state_json=?,updated=? "
                "WHERE id=? AND member=?",
                (
                    str(title or existing["title"]).strip()[:180],
                    str(existing["course"] if course is None else course)[:180],
                    next_status,
                    json.dumps(state, ensure_ascii=False, default=str),
                    now,
                    project_id,
                    member,
                ),
            )
        _activate(conn, member, session_id, project_id, now)
    return get_project(member, project_id, path) or {"id": project_id}


def record_authoring_result(
    member: str,
    session_id: str,
    tool_name: str,
    args: Mapping[str, Any],
    result: Mapping[str, Any],
    path: Path | None = None,
) -> dict[str, Any] | None:
    """Advance the active checkpoint from an executed authoring action."""
    if result.get("status") not in {"done", "noop"}:
        return active_project(member, session_id, path)
    project = active_project(member, session_id, path)
    if not project:
        return None
    project_state = project.get("state")
    state: dict[str, Any] = dict(project_state) if isinstance(project_state, dict) else {}
    completed_value = state.get("completed_items")
    completed = list(completed_value) if isinstance(completed_value, list) else []
    changes_value = result.get("changes")
    changes: list[dict[str, Any]] = [
        dict(item) for item in changes_value if isinstance(item, dict)
    ] if isinstance(changes_value, list) else []
    labels = [
        f"{item.get('op', 'update')} {item.get('doctype', '')}: {item.get('name', '')}".strip()
        for item in changes if isinstance(item, dict)
    ]
    for label in labels:
        if label and label not in completed:
            completed.append(label)
    patch: dict[str, Any] = {"completed_items": completed[-100:]}
    course = None
    if tool_name == "manage_course":
        for item in changes:
            if isinstance(item, dict) and item.get("doctype") == "LMS Course":
                course = str(item.get("name") or "") or None
                break
    if tool_name in {"manage_chapter", "manage_lesson"}:
        patch["current_focus"] = str(args.get("title") or args.get("chapter") or args.get("lesson") or "")
    return checkpoint_project(
        member,
        session_id,
        project_id=str(project["id"]),
        course=course,
        status="authoring",
        patch=patch,
        path=path,
    )
