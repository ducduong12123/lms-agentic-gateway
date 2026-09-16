"""Durable action records and bounded, identity-scoped undo operations."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

DB = Path(__file__).resolve().parents[3] / "data" / "gateway.db"


def _conn(path: Path | None = None) -> sqlite3.Connection:
    db = Path(path) if path else DB
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS actions ("
        "id TEXT PRIMARY KEY, member TEXT NOT NULL, tool TEXT NOT NULL, "
        "args_json TEXT NOT NULL, result_json TEXT NOT NULL, undo_json TEXT, "
        "status TEXT NOT NULL, created REAL NOT NULL, undone_at REAL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_actions_member_created ON actions(member, created DESC)")
    conn.commit()
    return conn


def _decode(row: sqlite3.Row | dict) -> dict:
    item = dict(row)
    for key in ("args_json", "result_json", "undo_json"):
        raw = item.pop(key, None)
        target = key.removesuffix("_json")
        if raw:
            try:
                item[target] = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                item[target] = raw
        else:
            item[target] = None
    return item


def record_action(
    member: str,
    tool: str,
    args: dict,
    result: dict,
    undo: dict | None,
    status: str = "done",
    action_id: str | None = None,
    path: Path | None = None,
) -> str:
    action_id = action_id or "act_" + uuid.uuid4().hex[:12]
    with _conn(path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO actions(id,member,tool,args_json,result_json,undo_json,status,created,undone_at) "
            "VALUES(?,?,?,?,?,?,?,?,NULL)",
            (
                action_id,
                str(member or ""),
                str(tool or ""),
                json.dumps(args or {}, ensure_ascii=False, default=str),
                json.dumps(result or {}, ensure_ascii=False, default=str),
                json.dumps(undo, ensure_ascii=False, default=str) if undo else None,
                str(status or "done"),
                time.time(),
            ),
        )
    return action_id


def get_action(action_id: str, path: Path | None = None) -> dict | None:
    with _conn(path) as conn:
        row = conn.execute("SELECT * FROM actions WHERE id=?", (str(action_id),)).fetchone()
    return _decode(row) if row else None


def recent_actions(member: str, limit: int = 20, path: Path | None = None) -> list[dict]:
    with _conn(path) as conn:
        rows = conn.execute(
            "SELECT * FROM actions WHERE member=? ORDER BY created DESC LIMIT ?",
            (str(member or ""), max(1, min(int(limit), 100))),
        ).fetchall()
    return [_decode(row) for row in rows]


def mark_undone(action_id: str, path: Path | None = None) -> bool:
    with _conn(path) as conn:
        changed = conn.execute(
            "UPDATE actions SET status='undone', undone_at=? WHERE id=? AND status='done'",
            (time.time(), str(action_id)),
        ).rowcount
    return changed == 1


def _local_undo(undo: dict, member: str) -> None:
    op = str(undo.get("op") or "")
    if op == "delete_review_set":
        from gateway.runtime import features
        if not features.delete_review_set(str(undo["set_id"]), member=member):
            raise ValueError("review set not found")
        return
    if op == "cancel_review":
        from gateway.runtime import features
        if not features.cancel_scheduled_review(str(undo["id"]), member=member):
            raise ValueError("scheduled review not found")
        return
    if op == "restore_pref":
        from gateway.runtime import learner
        learner.set_member_pref(
            str(undo["member"]),
            bool(undo.get("daily_plan_opt_in")),
            str(undo.get("quiet_hours") or ""),
        )
        return
    raise ValueError("unsupported local undo operation")

def undo_action(action_id: str, frappe, member: str | None = None, path: Path | None = None) -> dict:
    record = get_action(action_id, path)
    if not record:
        raise ValueError("action not found")
    if member is not None and record["member"] != str(member):
        raise PermissionError("action belongs to another member")
    if record["status"] != "done" or not record.get("undo"):
        raise ValueError("action cannot be undone")
    undo = dict(record["undo"])
    expires_at = undo.get("expires_at")
    if expires_at is not None and time.time() > float(expires_at):
        raise ValueError("undo window expired")
    op = str(undo.get("op") or "")
    if op == "delete":
        result = frappe.delete_document(str(undo["doctype"]), str(undo["name"]))
    elif op == "restore":
        result = frappe.update_document(str(undo["doctype"]), str(undo["name"]), dict(undo.get("fields") or {}))
    elif op in {"delete_review_set", "cancel_review"}:
        _local_undo(undo, record["member"])
        result = {"ok": True}
    else:
        raise ValueError("unsupported undo operation")
    if not mark_undone(action_id, path):
        raise ValueError("action was already undone")
    return {"action_id": action_id, "status": "undone", "result": result}
