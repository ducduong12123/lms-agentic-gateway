"""Durable write plans for Tier-1 plan → apply.

A write plan is a preview of Frappe writes built without touching Frappe.
Approval references the plan instead of raw args. Apply validates
``expected_modified`` (optimistic concurrency) and executes only approved items.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

DB = Path(__file__).resolve().parents[3] / "data" / "gateway.db"

REVERSIBLE = "reversible"
COMPENSATING = "compensating"
IRREVERSIBLE = "irreversible"

REVERSIBILITY_LABELS = {
    REVERSIBLE: "Lùi được",
    COMPENSATING: "Bù trừ được",
    IRREVERSIBLE: "Không lùi được",
}

_VALID_REVERSIBILITY = {REVERSIBLE, COMPENSATING, IRREVERSIBLE}
_VALID_PLAN_STATUS = {"pending", "applied", "partially_applied", "rejected", "failed"}
_VALID_ITEM_STATUS = {"pending", "approved", "rejected", "edited"}


def reversibility_label(value: str) -> str:
    return REVERSIBILITY_LABELS.get(str(value or ""), "Không lùi được")


def reversibility_for(tool: str, operation: str, has_questions: bool = False) -> str:
    """Classify a write verb operation for honest pre-approval labels."""
    tool = str(tool or "")
    operation = str(operation or "").casefold()
    if operation == "delete":
        return IRREVERSIBLE
    if tool == "message_students":
        return IRREVERSIBLE
    if operation == "create":
        return COMPENSATING
    if tool == "manage_quiz" and operation == "update" and has_questions:
        return COMPENSATING
    return REVERSIBLE


def requires_typed_confirm(tool: str, operation: str, reversibility: str) -> bool:
    return str(operation or "").casefold() == "delete" or str(reversibility or "") == IRREVERSIBLE


def _conn(path: Path | None = None) -> sqlite3.Connection:
    db = Path(path) if path else DB
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS write_plan ("
        "id TEXT PRIMARY KEY, tool TEXT NOT NULL, member TEXT NOT NULL, "
        "args_json TEXT NOT NULL, items_json TEXT NOT NULL, changes_json TEXT NOT NULL, "
        "reversibility TEXT NOT NULL, expected_modified_json TEXT NOT NULL, "
        "status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_write_plan_member ON write_plan(member, updated DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_write_plan_status ON write_plan(status, updated DESC)")
    conn.commit()
    return conn


def _encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _decode_plan(row: sqlite3.Row) -> dict:
    item = dict(row)
    for key, target in (
        ("args_json", "args"),
        ("items_json", "items"),
        ("changes_json", "changes"),
        ("expected_modified_json", "expected_modified"),
    ):
        raw = item.pop(key, None)
        try:
            item[target] = json.loads(raw) if raw else ([] if target != "args" and target != "expected_modified" else ({}))
        except (TypeError, json.JSONDecodeError):
            item[target] = [] if target in {"items", "changes"} else {}
    if not isinstance(item.get("args"), dict):
        item["args"] = {}
    if not isinstance(item.get("items"), list):
        item["items"] = []
    if not isinstance(item.get("changes"), list):
        item["changes"] = []
    if not isinstance(item.get("expected_modified"), dict):
        item["expected_modified"] = {}
    item["reversibility_label"] = reversibility_label(str(item.get("reversibility") or ""))
    return item


def create_plan(
    tool: str,
    member: str,
    args: dict,
    items: list[dict],
    changes: list[dict],
    reversibility: str,
    expected_modified: dict | None = None,
    path: Path | None = None,
) -> dict:
    if str(reversibility or "") not in _VALID_REVERSIBILITY:
        raise ValueError("invalid reversibility")
    normalized_items = []
    for index, raw in enumerate(list(items or [])):
        entry = dict(raw or {})
        entry.setdefault("id", f"item_{index + 1}")
        entry.setdefault("label", str(entry.get("label") or f"Mục {index + 1}"))
        entry.setdefault("kind", "field")
        entry.setdefault("preview", {})
        entry.setdefault("payload", {})
        entry.setdefault("status", "pending")
        if str(entry.get("status") or "") not in _VALID_ITEM_STATUS:
            entry["status"] = "pending"
        normalized_items.append(entry)
    plan_id = "plan_" + uuid.uuid4().hex[:12]
    now = time.time()
    with _conn(path) as conn:
        conn.execute(
            "INSERT INTO write_plan(id,tool,member,args_json,items_json,changes_json,"
            "reversibility,expected_modified_json,status,created,updated) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                plan_id, str(tool or ""), str(member or ""), _encode(dict(args or {})),
                _encode(normalized_items), _encode(list(changes or [])),
                str(reversibility), _encode(dict(expected_modified or {})),
                "pending", now, now,
            ),
        )
    plan = get_plan(plan_id, path)
    assert plan is not None
    return plan


def get_plan(plan_id: str, path: Path | None = None) -> dict | None:
    with _conn(path) as conn:
        row = conn.execute("SELECT * FROM write_plan WHERE id=?", (str(plan_id or ""),)).fetchone()
    return _decode_plan(row) if row else None


def mark_plan_status(plan_id: str, status: str, path: Path | None = None) -> dict | None:
    if str(status or "") not in _VALID_PLAN_STATUS:
        raise ValueError("invalid plan status")
    with _conn(path) as conn:
        conn.execute(
            "UPDATE write_plan SET status=?,updated=? WHERE id=?",
            (str(status), time.time(), str(plan_id or "")),
        )
    return get_plan(plan_id, path)


def patch_item(plan_id: str, item_id: str, payload: dict, path: Path | None = None) -> dict | None:
    plan = get_plan(plan_id, path)
    if not plan or plan.get("status") != "pending":
        return None
    found = False
    for entry in plan["items"]:
        if str(entry.get("id") or "") == str(item_id or ""):
            entry["payload"] = dict(payload or {})
            entry["status"] = "edited"
            preview = entry.get("preview")
            if isinstance(preview, dict):
                preview["after"] = dict(payload or {})
            found = True
    if not found:
        return None
    with _conn(path) as conn:
        conn.execute(
            "UPDATE write_plan SET items_json=?,updated=? WHERE id=?",
            (_encode(plan["items"]), time.time(), str(plan_id or "")),
        )
    return get_plan(plan_id, path)


def set_item_status(
    plan_id: str, item_id: str, status: str, path: Path | None = None,
) -> dict | None:
    if str(status or "") not in {"approved", "rejected", "pending"}:
        raise ValueError("invalid item status")
    plan = get_plan(plan_id, path)
    if not plan or plan.get("status") != "pending":
        return None
    found = False
    for entry in plan["items"]:
        if str(entry.get("id") or "") == str(item_id or ""):
            entry["status"] = str(status)
            found = True
    if not found:
        return None
    with _conn(path) as conn:
        conn.execute(
            "UPDATE write_plan SET items_json=?,updated=? WHERE id=?",
            (_encode(plan["items"]), time.time(), str(plan_id or "")),
        )
    return get_plan(plan_id, path)


def approved_items(plan: dict) -> list[dict]:
    items = plan.get("items") if isinstance(plan, dict) else []
    if not isinstance(items, list):
        return []
    return [
        entry for entry in items
        if isinstance(entry, dict) and str(entry.get("status") or "") in {"pending", "approved", "edited"}
    ]


def plan_key(doctype: str, name: str) -> str:
    return f"{doctype}:{name}"
