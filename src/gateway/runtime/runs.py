"""Durable agent run and tool-step state for pause/resume workflows."""
from __future__ import annotations

import json
import time
from sqlite3 import Row
import uuid
from pathlib import Path

from . import learner



def _migrate(path: Path | None = None) -> None:
    with learner._conn(path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_run ("
            "id TEXT PRIMARY KEY, session_id TEXT NOT NULL, member TEXT NOT NULL, goal TEXT NOT NULL, "
            "bundles_json TEXT NOT NULL DEFAULT '[]', plan_json TEXT NOT NULL DEFAULT '{}', "
            "status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_run_session "
            "ON agent_run(member,session_id,updated DESC)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_step ("
            "id TEXT PRIMARY KEY, run_id TEXT NOT NULL, ordinal INTEGER NOT NULL, label TEXT NOT NULL, "
            "tool TEXT NOT NULL DEFAULT '', args_json TEXT NOT NULL DEFAULT '{}', "
            "result_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL, approval_id TEXT, "
            "attempts INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, updated REAL NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_step_run ON agent_step(run_id,ordinal ASC)"
        )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)[:50000]


def _decode(value: str, fallback: object) -> object:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _row(row: Row) -> dict[str, object]:
    result = dict(row)
    result["bundles"] = _decode(str(result.pop("bundles_json", "[]")), [])
    result["plan"] = _decode(str(result.pop("plan_json", "{}")), {})
    return result


def get_run(run_id: str, member: str = "", path: Path | None = None) -> dict[str, object] | None:
    _migrate(path)
    query = "SELECT * FROM agent_run WHERE id=?"
    args: list[object] = [str(run_id)]
    if member:
        query += " AND member=?"
        args.append(str(member))
    with learner._conn(path) as conn:
        row = conn.execute(query, args).fetchone()
        if not row:
            return None
        run = _row(row)
        steps = conn.execute(
            "SELECT * FROM agent_step WHERE run_id=? ORDER BY ordinal ASC", (run_id,)
        ).fetchall()
    run["tool_steps"] = [
        {
            **dict(step),
            "args": _decode(str(step["args_json"] or "{}"), {}),
            "result": _decode(str(step["result_json"] or "{}"), {}),
        }
        for step in steps
    ]
    for step in run["tool_steps"]:
        step.pop("args_json", None)
        step.pop("result_json", None)
    return run


def active_run(member: str, session_id: str, path: Path | None = None) -> dict[str, object] | None:
    _migrate(path)
    with learner._conn(path) as conn:
        row = conn.execute(
            "SELECT id FROM agent_run WHERE member=? AND session_id=? "
            "AND status IN ('running','waiting_approval') ORDER BY updated DESC LIMIT 1",
            (str(member), str(session_id)),
        ).fetchone()
    return get_run(str(row["id"]), member, path) if row else None


def start_or_resume(
    member: str,
    session_id: str,
    plan: dict[str, object],
    path: Path | None = None,
) -> dict[str, object]:
    current = active_run(member, session_id, path)
    if current:
        return current
    now = time.time()
    run_id = "run_" + uuid.uuid4().hex[:14]
    with learner._conn(path) as conn:
        conn.execute(
            "INSERT INTO agent_run(id,session_id,member,goal,bundles_json,plan_json,status,created,updated) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                run_id, str(session_id), str(member), str(plan.get("goal") or "")[:500],
                _json(plan.get("bundles") or []), _json(plan), "running", now, now,
            ),
        )
    return get_run(run_id, member, path) or {"id": run_id, "status": "running", "plan": plan}


def start_step(run_id: str, label: str, tool: str, args: dict[str, object], path: Path | None = None) -> str:
    _migrate(path)
    now = time.time()
    step_id = "step_" + uuid.uuid4().hex[:14]
    with learner._conn(path) as conn:
        ordinal = int(conn.execute(
            "SELECT COALESCE(MAX(ordinal),0)+1 FROM agent_step WHERE run_id=?", (run_id,)
        ).fetchone()[0])
        conn.execute(
            "INSERT INTO agent_step(id,run_id,ordinal,label,tool,args_json,status,created,updated) "
            "VALUES(?,?,?,?,?,?, 'running',?,?)",
            (step_id, run_id, ordinal, str(label)[:180], str(tool), _json(args), now, now),
        )
        conn.execute("UPDATE agent_run SET updated=? WHERE id=?", (now, run_id))
    return step_id


def finish_step(
    step_id: str,
    result: object,
    status: str = "completed",
    approval_id: str = "",
    path: Path | None = None,
) -> dict[str, object] | None:
    _migrate(path)
    if status not in {"completed", "failed", "waiting_approval"}:
        raise ValueError("invalid agent step status")
    now = time.time()
    with learner._conn(path) as conn:
        row = conn.execute("SELECT run_id FROM agent_step WHERE id=?", (step_id,)).fetchone()
        if not row:
            return None
        run_id = str(row["run_id"])
        conn.execute(
            "UPDATE agent_step SET result_json=?,status=?,approval_id=?,updated=? WHERE id=?",
            (_json(result), status, approval_id or None, now, step_id),
        )
        run_status = "waiting_approval" if status == "waiting_approval" else "running"
        conn.execute("UPDATE agent_run SET status=?,updated=? WHERE id=?", (run_status, now, run_id))
    return get_run(run_id, path=path)


def complete_run(run_id: str, status: str = "completed", path: Path | None = None) -> None:
    if status not in {"completed", "failed", "cancelled"}:
        raise ValueError("invalid terminal run status")
    _migrate(path)
    with learner._conn(path) as conn:
        conn.execute("UPDATE agent_run SET status=?,updated=? WHERE id=?", (status, time.time(), run_id))


def complete_approval(approval_id: str, result: object, path: Path | None = None) -> dict[str, object] | None:
    _migrate(path)
    with learner._conn(path) as conn:
        step = conn.execute(
            "SELECT id,run_id FROM agent_step WHERE approval_id=? AND status='waiting_approval'",
            (str(approval_id),),
        ).fetchone()
    if not step:
        return None
    failed = isinstance(result, dict) and bool(result.get("error"))
    finish_step(str(step["id"]), result, "failed" if failed else "completed", approval_id, path)
    if failed:
        complete_run(str(step["run_id"]), "failed", path)
    else:
        with learner._conn(path) as conn:
            conn.execute(
                "UPDATE agent_run SET status='running',updated=? WHERE id=?",
                (time.time(), str(step["run_id"])),
            )
    return get_run(str(step["run_id"]), path=path)
