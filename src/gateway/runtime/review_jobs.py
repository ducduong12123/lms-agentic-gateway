"""Trạng thái bền của job nhận xét bài dự án (SQLite riêng, cùng file data/gateway.db).

Một job là duy nhất theo (project_submission, rewrite_of): lms_copilot gửi lại cùng yêu cầu
(retry của RQ, người vận hành bấm lại) thì nhận về job cũ, không chạy thêm lần nữa.
Job chưa xong (queued / running / unreported) được chạy tiếp khi Gateway khởi động lại.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

DB = Path(__file__).resolve().parents[3] / "data" / "gateway.db"

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
# Đã báo cho lms_copilot là cần giáo viên chấm tay (thiếu rubric, AI trả sai hai lần...).
MANUAL = "manual"
FAILED = "failed"
# Lỗi mà chưa báo được về lms_copilot (Frappe cũng lỗi): khởi động lại sẽ báo tiếp.
UNREPORTED = "unreported"

UNFINISHED = (QUEUED, RUNNING, UNREPORTED)
MAX_ATTEMPTS = 3


def _conn(path: Path | None = None) -> sqlite3.Connection:
    p = Path(path) if path else DB
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS review_job ("
        "id TEXT PRIMARY KEY, project_submission TEXT NOT NULL, rewrite_of TEXT NOT NULL DEFAULT '', "
        "site TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, "
        "attempts INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', "
        "result_json TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL, updated REAL NOT NULL, "
        "UNIQUE(project_submission, rewrite_of))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_review_job_status ON review_job(status, updated)")
    return conn


def _row(row: sqlite3.Row | None) -> dict | None:
    if not row:
        return None
    job = dict(row)
    try:
        job["result"] = json.loads(job.pop("result_json") or "{}")
    except (TypeError, ValueError):
        job["result"] = {}
    job["rewrite_of"] = job["rewrite_of"] or None
    return job


def get(job_id: str) -> dict | None:
    with _conn() as conn:
        return _row(conn.execute("SELECT * FROM review_job WHERE id=?", (str(job_id),)).fetchone())


def find(project_submission: str, rewrite_of: str | None = None) -> dict | None:
    with _conn() as conn:
        return _row(conn.execute(
            "SELECT * FROM review_job WHERE project_submission=? AND rewrite_of=?",
            (str(project_submission), str(rewrite_of or "")),
        ).fetchone())


def enqueue(project_submission: str, rewrite_of: str | None = None, site: str = "",
            model: str = "") -> tuple[dict, bool]:
    """Tạo job nếu chưa có. Trả (job, cần_chạy). Job FAILED được xếp hàng lại."""
    now = time.time()
    job_id = "rvj_" + uuid.uuid4().hex[:16]
    with _conn() as conn:
        inserted = conn.execute(
            "INSERT OR IGNORE INTO review_job(id,project_submission,rewrite_of,site,model,status,"
            "created,updated) VALUES(?,?,?,?,?,?,?,?)",
            (job_id, str(project_submission), str(rewrite_of or ""), str(site or "")[:500],
             str(model or "")[:140], QUEUED, now, now),
        ).rowcount
        if not inserted:
            requeued = conn.execute(
                "UPDATE review_job SET status=?, attempts=0, error='', model=?, updated=? "
                "WHERE project_submission=? AND rewrite_of=? AND status=?",
                (QUEUED, str(model or "")[:140], now, str(project_submission), str(rewrite_of or ""),
                 FAILED),
            ).rowcount
            inserted = requeued
    job = find(project_submission, rewrite_of)
    assert job is not None
    return job, bool(inserted)


def claim(job_id: str) -> dict | None:
    """Chuyển job sang RUNNING và tăng số lần thử; None nếu job đã xong."""
    with _conn() as conn:
        changed = conn.execute(
            "UPDATE review_job SET status=?, attempts=attempts+1, updated=? "
            "WHERE id=? AND status IN (?,?)",
            (RUNNING, time.time(), str(job_id), QUEUED, RUNNING),
        ).rowcount
    return get(job_id) if changed else None


def finish(job_id: str, status: str, error: str = "", result: dict | None = None) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE review_job SET status=?, error=?, result_json=?, updated=? WHERE id=?",
            (status, str(error or "")[:2000],
             json.dumps(result or {}, ensure_ascii=False, default=str)[:20000], time.time(),
             str(job_id)),
        )


def unfinished() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM review_job WHERE status IN (?,?,?) ORDER BY created ASC", UNFINISHED
        ).fetchall()
    return [job for job in (_row(row) for row in rows) if job]
