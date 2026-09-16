"""Single-use approvals and audit records stored outside Frappe."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

DB = Path(__file__).resolve().parents[3] / "data" / "gateway.db"


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS approvals ("
        "id TEXT PRIMARY KEY, tool TEXT NOT NULL, args TEXT NOT NULL, "
        "status TEXT NOT NULL, created REAL NOT NULL)"
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(approvals)")}
    additions = {
        "requested_by": "TEXT",
        "requested_role": "TEXT",
        "required_roles": "TEXT",
        "approved_by": "TEXT",
        "approved_at": "REAL",
        "executed_at": "REAL",
        "result": "TEXT",
        "plan_id": "TEXT",
    }
    for name, column_type in additions.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE approvals ADD COLUMN {name} {column_type}")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit ("
        "id TEXT PRIMARY KEY, role TEXT, tool TEXT, args TEXT, result TEXT, created REAL)"
    )
    audit_existing = {row[1] for row in conn.execute("PRAGMA table_info(audit)")}
    if "actor" not in audit_existing:
        conn.execute("ALTER TABLE audit ADD COLUMN actor TEXT")
    conn.commit()
    return conn

def request_approval(
    tool: str,
    args: dict,
    requested_by: str,
    requested_role: str,
    required_roles: set[str] | None = None,
    plan_id: str | None = None,
) -> str:
    approval_id = uuid.uuid4().hex
    roles = sorted(required_roles or {"admin"})
    with _conn() as conn:
        conn.execute(
            "INSERT INTO approvals "
            "(id, tool, args, status, created, requested_by, requested_role, required_roles, plan_id) "
            "VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
            (
                approval_id,
                tool,
                json.dumps(args, ensure_ascii=False),
                time.time(),
                requested_by,
                requested_role,
                json.dumps(roles),
                str(plan_id or "") or None,
            ),
        )
    return approval_id


def peek_approval(approval_id: str, approved_by: str, approver_role: str) -> dict | None:
    """Read a pending approval and check role without consuming its single use."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM approvals WHERE id=? AND status='pending'", (approval_id,)
        ).fetchone()
        if not row:
            return None
        required_roles = set(json.loads(row["required_roles"] or "[]"))
        if approver_role not in required_roles:
            raise PermissionError("role is not allowed to approve this action")
        stored_plan = row["plan_id"] if "plan_id" in row.keys() else ""
        return {
            "id": row["id"], "tool": row["tool"], "args": json.loads(row["args"]),
            "plan_id": str(stored_plan or ""),
        }


def approve(approval_id: str, approved_by: str, approver_role: str) -> dict | None:
    """Atomically claim a pending approval. A claimed ID cannot be reused."""
    with _conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM approvals WHERE id=? AND status='pending'", (approval_id,)
        ).fetchone()
        if not row:
            return None
        required_roles = set(json.loads(row["required_roles"] or "[]"))
        if approver_role not in required_roles:
            raise PermissionError("role is not allowed to approve this action")
        updated = conn.execute(
            "UPDATE approvals SET status='approved', approved_by=?, approved_at=? "
            "WHERE id=? AND status='pending'",
            (approved_by, time.time(), approval_id),
        )
        if updated.rowcount != 1:
            return None
        stored_plan = row["plan_id"] if "plan_id" in row.keys() else ""
        return {
            "id": row["id"], "tool": row["tool"], "args": json.loads(row["args"]),
            "plan_id": str(stored_plan or ""),
        }


def mark_executed(approval_id: str, result) -> None:
    result_json = json.dumps(result, ensure_ascii=False, default=str)[:12000]
    with _conn() as conn:
        conn.execute(
            "UPDATE approvals SET status='executed', executed_at=?, result=? "
            "WHERE id=? AND status='approved'",
            (time.time(), result_json, approval_id),
        )


def audit(role: str, tool: str, args: dict, result, actor: str = "") -> None:
    try:
        result_json = json.dumps(result, ensure_ascii=False, default=str)[:4000]
    except Exception:
        result_json = str(result)[:4000]
    with _conn() as conn:
        conn.execute(
            "INSERT INTO audit (id, role, tool, args, result, created, actor) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex,
                role,
                tool,
                json.dumps(args, ensure_ascii=False)[:4000],
                result_json,
                time.time(),
                actor,
            ),
        )
def pending_for(role: str, member: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id,tool,args,status,created,requested_by,required_roles,plan_id "
            "FROM approvals WHERE status='pending' AND (requested_by=? OR required_roles LIKE ?)"
            " ORDER BY created DESC LIMIT 50",
            (member, f'%\"{role}\"%'),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["args"] = json.loads(item.pop("args") or "{}")
        item["required_roles"] = json.loads(item["required_roles"] or "[]")
        item["plan_id"] = str(item.get("plan_id") or "")
        out.append(item)
    return out
