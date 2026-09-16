"""ITS learner model cho engine: concept/evidence/mastery append-only.

Phù hợp engine-plan.html:
- Frappe là nguồn sự thật; engine chỉ suy ra learner state trong DB riêng.
- evidence append-only, không sửa; mastery là giá trị dẫn xuất, recompute được.
- Chỉ concept approved mới dùng cho quyết định với học viên.
- Trọng số/outcome mặc định theo bảng evidence của engine-plan.

SQLite thay Postgres cho pilot local; schema giữ nguyên tên/cột để migrate sau.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

from gateway.runtime.trace import trace as _trace

DB = Path(__file__).resolve().parents[3] / "data" / "gateway.db"

ETA = 0.3
INITIAL_P = 0.3
FORGET_HALF_LIFE_DAYS = 45.0

EVIDENCE_WEIGHTS = {
    "quiz_item": 1.0,
    "assignment": 1.2,
    "programming": 0.8,
    "dialogue_check": 1.5,
    "feynman": 1.5,
    "lesson_complete": 0.2,
    "rewatch": 0.2,
    "feedback_correction": 0.6,
}


def _conn(path: Path | None = None) -> sqlite3.Connection:
    p = Path(path) if path else DB
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS concept ("
        "id TEXT PRIMARY KEY, course TEXT NOT NULL DEFAULT '', slug TEXT NOT NULL DEFAULT '', "
        "label TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', "
        "prereq_ids TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'draft', "
        "source_lesson TEXT NOT NULL DEFAULT '', content_hash TEXT NOT NULL DEFAULT '', "
        "created REAL NOT NULL, updated REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS lesson_concept ("
        "lesson TEXT NOT NULL, concept_id TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1.0, "
        "PRIMARY KEY (lesson, concept_id))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS question_concept ("
        "question TEXT NOT NULL, concept_id TEXT NOT NULL, "
        "PRIMARY KEY (question, concept_id))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS evidence ("
        "id TEXT PRIMARY KEY, member TEXT NOT NULL, concept_id TEXT NOT NULL, "
        "kind TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1.0, outcome REAL NOT NULL DEFAULT 0, "
        "observed_at REAL NOT NULL, ref_doctype TEXT NOT NULL DEFAULT '', "
        "ref_name TEXT NOT NULL DEFAULT '', ref_modified TEXT NOT NULL DEFAULT '')"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_idempotent "
        "ON evidence(ref_doctype, ref_name, ref_modified, concept_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_member ON evidence(member, concept_id, observed_at)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mastery ("
        "member TEXT NOT NULL, concept_id TEXT NOT NULL, p REAL NOT NULL DEFAULT 0.3, "
        "n_evidence INTEGER NOT NULL DEFAULT 0, last_evidence_at REAL NOT NULL DEFAULT 0, "
        "PRIMARY KEY (member, concept_id))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sync_cursor (doctype TEXT PRIMARY KEY, last_modified TEXT NOT NULL DEFAULT '')"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS learning_session ("
        "id TEXT PRIMARY KEY, member TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'chat', "
        "concept_id TEXT NOT NULL DEFAULT '', transcript TEXT NOT NULL DEFAULT '', "
        "rubric_score REAL, created REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS member_pref ("
        "member TEXT PRIMARY KEY, daily_plan_opt_in INTEGER NOT NULL DEFAULT 0, quiet_hours TEXT NOT NULL DEFAULT '')"
    )
    conn.commit()
    return conn


def _content_hash(*parts: str) -> str:
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def upsert_concept(
    label: str,
    course: str = "",
    description: str = "",
    prereq_ids: list[str] | None = None,
    source_lesson: str = "",
    status: str = "draft",
    path: Path | None = None,
) -> dict:
    """Tạo concept mới; content đổi thì tạo bản mới, không sửa bản đã duyệt."""
    label = str(label or "").strip()
    if not label:
        raise ValueError("label trống.")
    course = str(course or "").strip()[:256]
    description = str(description or "").strip()[:2000]
    source_lesson = str(source_lesson or "").strip()[:256]
    prereqs = [str(item) for item in (prereq_ids or []) if str(item).strip()][:20]
    status = "approved" if status == "approved" else "draft"
    slug = "-".join(label.casefold().split())[:120] or uuid.uuid4().hex[:8]
    content_hash = _content_hash(course, label, description, source_lesson)
    now = time.time()
    conn = _conn(path)
    row = conn.execute(
        "SELECT id, status FROM concept WHERE course=? AND slug=? AND content_hash=?",
        (course, slug, content_hash),
    ).fetchone()
    if row:
        conn.close()
        return {"id": row["id"], "status": row["status"], "reused": True}
    concept_id = "cpt_" + uuid.uuid4().hex[:8]
    conn.execute(
        "INSERT INTO concept(id, course, slug, label, description, prereq_ids, status,"
        " source_lesson, content_hash, created, updated)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            concept_id, course, slug, label, description, json.dumps(prereqs),
            status, source_lesson, content_hash, now, now,
        ),
    )
    conn.commit()
    conn.close()
    return {"id": concept_id, "status": status, "reused": False}


def approve_concept(concept_id: str, path: Path | None = None) -> bool:
    conn = _conn(path)
    cur = conn.execute(
        "UPDATE concept SET status='approved', updated=? WHERE id=?",
        (time.time(), str(concept_id or "")),
    )
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def link_lesson_concept(
    lesson: str, concept_id: str, weight: float = 1.0, path: Path | None = None
) -> None:
    conn = _conn(path)
    conn.execute(
        "INSERT OR REPLACE INTO lesson_concept(lesson, concept_id, weight) VALUES (?,?,?)",
        (str(lesson or ""), str(concept_id or ""), float(weight or 1.0)),
    )
    conn.commit()
    conn.close()


def link_question_concept(
    question: str, concept_id: str, path: Path | None = None
) -> None:
    conn = _conn(path)
    conn.execute(
        "INSERT OR IGNORE INTO question_concept(question, concept_id) VALUES (?,?)",
        (str(question or ""), str(concept_id or "")),
    )
    conn.commit()
    conn.close()


def record_evidence(
    member: str,
    concept_id: str,
    kind: str,
    outcome: float,
    ref_doctype: str = "",
    ref_name: str = "",
    ref_modified: str = "",
    weight: float | None = None,
    observed_at: float | None = None,
    path: Path | None = None,
) -> dict:
    """Ghi evidence append-only, idempotent theo (doctype, name, modified, concept)."""
    member = str(member or "").strip()
    concept_id = str(concept_id or "").strip()
    if not member or member == "Guest":
        raise ValueError("member chưa đăng nhập.")
    if not concept_id:
        raise ValueError("concept_id trống.")
    kind = str(kind or "quiz_item").strip()
    try:
        outcome = max(0.0, min(1.0, float(outcome)))
    except (TypeError, ValueError):
        outcome = 0.0
    if weight is None:
        weight = EVIDENCE_WEIGHTS.get(kind, 0.5)
    try:
        weight = max(0.0, min(3.0, float(weight)))
    except (TypeError, ValueError):
        weight = 0.5
    observed = float(observed_at or time.time())
    evidence_id = "evd_" + uuid.uuid4().hex[:8]
    conn = _conn(path)
    try:
        conn.execute(
            "INSERT INTO evidence(id, member, concept_id, kind, weight, outcome,"
            " observed_at, ref_doctype, ref_name, ref_modified)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                evidence_id, member, concept_id, kind, weight, outcome, observed,
                str(ref_doctype or ""), str(ref_name or ""), str(ref_modified or ""),
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        row = conn.execute(
            "SELECT id FROM evidence WHERE ref_doctype=? AND ref_name=? AND ref_modified=?"
            " AND concept_id=?",
            (str(ref_doctype or ""), str(ref_name or ""), str(ref_modified or ""), concept_id),
        ).fetchone()
        conn.close()
        return {"id": row["id"] if row else evidence_id, "duplicate": True}
    conn.close()
    _apply_mastery(member, concept_id, outcome, float(weight), observed, path)
    try:
        _trace(
            "its", "evidence", member=member, concept_id=concept_id, kind=kind,
            outcome=outcome, weight=float(weight), evidence_id=evidence_id,
            ref_doctype=str(ref_doctype or ""), ref_name=str(ref_name or ""),
            ref_modified=str(ref_modified or ""),
        )
    except Exception:
        pass
    return {"id": evidence_id, "duplicate": False}

def _apply_mastery(
    member: str, concept_id: str, outcome: float, weight: float,
    observed_at: float, path: Path | None = None,
) -> float:
    conn = _conn(path)
    row = conn.execute(
        "SELECT p, n_evidence FROM mastery WHERE member=? AND concept_id=?",
        (member, concept_id),
    ).fetchone()
    current = float(row["p"]) if row else INITIAL_P
    count = int(row["n_evidence"]) if row else 0
    new_p = max(0.0, min(1.0, current + ETA * weight * (outcome - current)))
    conn.execute(
        "INSERT INTO mastery(member, concept_id, p, n_evidence, last_evidence_at)"
        " VALUES (?,?,?,?,?)"
        " ON CONFLICT(member, concept_id) DO UPDATE SET"
        " p=excluded.p, n_evidence=excluded.n_evidence, last_evidence_at=excluded.last_evidence_at",
        (member, concept_id, new_p, count + 1, observed_at),
    )
    conn.commit()
    conn.close()
    try:
        _trace(
            "its", "mastery", member=member, concept_id=concept_id,
            p_before=current, p_after=new_p, n_evidence=count + 1,
            outcome=outcome, weight=weight,
        )
    except Exception:
        pass
    return new_p


def recompute_mastery(
    member: str | None = None, path: Path | None = None
) -> dict[str, float]:
    """Chạy lại toàn bộ mastery từ evidence; dùng khi đổi công thức."""
    conn = _conn(path)
    if member:
        conn.execute("DELETE FROM mastery WHERE member=?", (member,))
        rows = conn.execute(
            "SELECT member, concept_id, outcome, weight, observed_at FROM evidence"
            " WHERE member=? ORDER BY observed_at ASC, id ASC",
            (member,),
        ).fetchall()
    else:
        conn.execute("DELETE FROM mastery")
        rows = conn.execute(
            "SELECT member, concept_id, outcome, weight, observed_at FROM evidence"
            " ORDER BY observed_at ASC, id ASC"
        ).fetchall()
    conn.commit()
    conn.close()
    states: dict[tuple[str, str], list] = {}
    for row in rows:
        key = (row["member"], row["concept_id"])
        current, count, _ = states.get(key, [INITIAL_P, 0, 0.0])
        current = max(0.0, min(1.0, current + ETA * float(row["weight"]) * (float(row["outcome"]) - current)))
        states[key] = [current, count + 1, float(row["observed_at"])]
    conn = _conn(path)
    for (user, concept_id), (value, count, observed_at) in states.items():
        conn.execute(
            "INSERT INTO mastery(member, concept_id, p, n_evidence, last_evidence_at)"
            " VALUES (?,?,?,?,?)",
            (user, concept_id, value, count, observed_at),
        )
    conn.commit()
    conn.close()
    return {f"{user}::{concept_id}": value for (user, concept_id), (value, _, _) in states.items()}


def decayed_p(raw_p: float, last_evidence_at: float, now: float | None = None) -> float:
    current = time.time() if now is None else float(now)
    days = max(0.0, (current - float(last_evidence_at or current)) / 86400.0)
    return float(raw_p) * (0.5 ** (days / FORGET_HALF_LIFE_DAYS))


def approved_concepts_for_course(course: str, path: Path | None = None) -> list[dict]:
    conn = _conn(path)
    rows = conn.execute(
        "SELECT id, slug, label, description FROM concept"
        " WHERE course=? AND status='approved' ORDER BY label ASC",
        (str(course or ""),),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def concepts_for_lesson(lesson: str, path: Path | None = None) -> list[dict]:
    conn = _conn(path)
    rows = conn.execute(
        "SELECT c.id, c.label, c.slug, lc.weight FROM lesson_concept lc"
        " JOIN concept c ON c.id=lc.concept_id"
        " WHERE lc.lesson=? AND c.status='approved' ORDER BY lc.weight DESC",
        (str(lesson or ""),),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def weak_concepts(
    member: str, course: str = "", limit: int = 3, path: Path | None = None,
    now: float | None = None,
) -> list[dict]:
    """Top concept yếu đã approved, kèm evidence gần nhất để giải thích vì sao."""
    member = str(member or "").strip()
    if not member or member == "Guest":
        return []
    limit = max(1, min(5, int(limit or 3)))
    current = time.time() if now is None else float(now)
    conn = _conn(path)
    if course:
        rows = conn.execute(
            "SELECT m.concept_id, m.p, m.n_evidence, m.last_evidence_at, c.label"
            " FROM mastery m JOIN concept c ON c.id=m.concept_id"
            " WHERE m.member=? AND c.status='approved' AND c.course=?"
            " ORDER BY m.p ASC LIMIT ?",
            (member, str(course), limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT m.concept_id, m.p, m.n_evidence, m.last_evidence_at, c.label"
            " FROM mastery m JOIN concept c ON c.id=m.concept_id"
            " WHERE m.member=? AND c.status='approved'"
            " ORDER BY m.p ASC LIMIT ?",
            (member, limit),
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        evidences = conn.execute(
            "SELECT kind, outcome, observed_at, ref_doctype, ref_name FROM evidence"
            " WHERE member=? AND concept_id=? ORDER BY observed_at DESC LIMIT 3",
            (member, row["concept_id"]),
        ).fetchall()
        out.append(
            {
                "concept_id": row["concept_id"],
                "label": row["label"],
                "p": float(row["p"]),
                "p_display": decayed_p(float(row["p"]), float(row["last_evidence_at"] or current), current),
                "n_evidence": int(row["n_evidence"]),
                "why": [dict(item) for item in evidences],
            }
        )
    conn.close()
    return out


def format_learner_block(
    weak: list[dict], lesson_concepts: list[dict] | None = None, max_items: int = 3
) -> str:
    chunks: list[str] = []
    if lesson_concepts:
        names = ", ".join(str(item.get("label") or "") for item in lesson_concepts[:6] if item.get("label"))
        if names:
            chunks.append(f"Bài đang mở liên quan concept: {names}.")
    lines: list[str] = []
    for item in (weak or [])[:max_items]:
        label = str(item.get("label") or item.get("concept_id") or "?")
        score = float(item.get("p_display", item.get("p", 0.0)))
        lines.append(f"- {label} (nắm vững khoảng {score:.0%})")
    if lines:
        chunks.append(
            "Concept yếu của người học (chỉ dùng concept đã duyệt, kèm nguồn khi trả lời):\n"
            + "\n".join(lines)
        )
    if not chunks:
        return ""
    return "\n".join(chunks)


def record_learning_session(
    member: str, mode: str = "chat", concept_id: str = "",
    transcript: str = "", rubric_score: float | None = None,
    path: Path | None = None,
) -> dict:
    """Lưu phiên học engine tạo evidence riêng (Feynman/check hội thoại)."""
    member = str(member or "").strip()
    if not member or member == "Guest":
        raise ValueError("member chưa đăng nhập.")
    mode = str(mode or "chat").strip()[:32] or "chat"
    session_id = "ses_" + uuid.uuid4().hex[:8]
    now = time.time()
    score = None
    if rubric_score is not None:
        try:
            score = max(0.0, min(1.0, float(rubric_score)))
        except (TypeError, ValueError):
            score = None
    conn = _conn(path)
    conn.execute(
        "INSERT INTO learning_session(id, member, mode, concept_id, transcript,"
        " rubric_score, created) VALUES (?,?,?,?,?,?,?)",
        (
            session_id, member, mode, str(concept_id or "")[:128],
            str(transcript or "")[:12000], score, now,
        ),
    )
    conn.commit()
    conn.close()
    if concept_id and score is not None:
        kind = "feynman" if mode == "feynman" else "dialogue_check"
        record_evidence(
            member, str(concept_id), kind, score,
            ref_doctype="learning_session", ref_name=session_id,
            ref_modified=str(int(now)), path=path,
        )
    return {"id": session_id, "mode": mode}


def set_member_pref(
    member: str, daily_plan_opt_in: bool | None = None,
    quiet_hours: str | None = None, path: Path | None = None,
) -> dict:
    member = str(member or "").strip()
    if not member or member == "Guest":
        raise ValueError("member chưa đăng nhập.")
    conn = _conn(path)
    row = conn.execute(
        "SELECT daily_plan_opt_in, quiet_hours FROM member_pref WHERE member=?",
        (member,),
    ).fetchone()
    opt_in = int(row["daily_plan_opt_in"]) if row else 0
    quiet = str(row["quiet_hours"] or "") if row else ""
    if daily_plan_opt_in is not None:
        opt_in = 1 if daily_plan_opt_in else 0
    if quiet_hours is not None:
        quiet = str(quiet_hours or "").strip()[:128]
    conn.execute(
        "INSERT INTO member_pref(member, daily_plan_opt_in, quiet_hours)"
        " VALUES (?,?,?)"
        " ON CONFLICT(member) DO UPDATE SET"
        " daily_plan_opt_in=excluded.daily_plan_opt_in, quiet_hours=excluded.quiet_hours",
        (member, opt_in, quiet),
    )
    conn.commit()
    conn.close()
    return {"member": member, "daily_plan_opt_in": bool(opt_in), "quiet_hours": quiet}


def get_member_pref(member: str, path: Path | None = None) -> dict:
    member = str(member or "").strip()
    conn = _conn(path)
    row = conn.execute(
        "SELECT daily_plan_opt_in, quiet_hours FROM member_pref WHERE member=?",
        (member,),
    ).fetchone()
    conn.close()
    if not row:
        return {"member": member, "daily_plan_opt_in": False, "quiet_hours": ""}
    return {
        "member": member,
        "daily_plan_opt_in": bool(int(row["daily_plan_opt_in"])),
        "quiet_hours": str(row["quiet_hours"] or ""),
    }


def mastery_snapshot(member: str, path: Path | None = None) -> list[dict]:
    conn = _conn(path)
    rows = conn.execute(
        "SELECT concept_id, p, n_evidence, last_evidence_at FROM mastery WHERE member=?",
        (str(member or ""),),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def math_mastery_delta(outcome: float, weight: float, current: float = INITIAL_P) -> float:
    return max(0.0, min(1.0, current + ETA * weight * (outcome - current)))
