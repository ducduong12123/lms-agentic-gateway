"""Approval + Audit tối giản, lưu sqlite để demo. Prod thay bằng DB thật."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

DB = Path(__file__).resolve().parents[3] / "data" / "gateway.db"


def _conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute(
        "CREATE TABLE IF NOT EXISTS approvals"
        "(id TEXT PRIMARY KEY, tool TEXT, args TEXT, status TEXT, created REAL)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS audit"
        "(id TEXT PRIMARY KEY, role TEXT, tool TEXT, args TEXT, result TEXT, created REAL)"
    )
    return c


def request_approval(tool: str, args: dict) -> str:
    aid = uuid.uuid4().hex[:8]
    c = _conn()
    c.execute(
        "INSERT INTO approvals VALUES (?,?,?,?,?)",
        (aid, tool, json.dumps(args, ensure_ascii=False), "pending", time.time()),
    )
    c.commit()
    c.close()
    return aid


def approve(aid: str) -> dict | None:
    c = _conn()
    row = c.execute("SELECT tool, args FROM approvals WHERE id=?", (aid,)).fetchone()
    if not row:
        c.close()
        return None
    c.execute("UPDATE approvals SET status='approved' WHERE id=?", (aid,))
    c.commit()
    c.close()
    return {"tool": row[0], "args": json.loads(row[1])}


def audit(role: str, tool: str, args: dict, result):
    c = _conn()
    try:
        result_s = json.dumps(result, ensure_ascii=False, default=str)[:4000]
    except Exception:
        result_s = str(result)[:4000]
    c.execute(
        "INSERT INTO audit VALUES (?,?,?,?,?,?)",
        (uuid.uuid4().hex[:8], role, tool, json.dumps(args, ensure_ascii=False)[:4000], result_s, time.time()),
    )
    c.commit()
    c.close()
