"""AI-native learning features stored entirely outside Frappe."""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from gateway.runtime import learner


def _migrate(path: Path | None = None) -> None:
    with learner._conn(path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS daily_plan ("
            "id TEXT PRIMARY KEY, member TEXT NOT NULL, plan_date TEXT NOT NULL, "
            "content TEXT NOT NULL, link TEXT NOT NULL DEFAULT '/ai/plan', created REAL NOT NULL, "
            "notified_at REAL, UNIQUE(member, plan_date))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_action ("
            "id TEXT PRIMARY KEY, actor TEXT NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL, "
            "course TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL, status TEXT NOT NULL, "
            "created REAL NOT NULL, approved_by TEXT, approved_at REAL, executed_at REAL, result TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS route_prompt ("
            "member TEXT NOT NULL, lesson TEXT NOT NULL, prompt_date TEXT NOT NULL, "
            "decision TEXT NOT NULL DEFAULT 'shown', created REAL NOT NULL, "
            "PRIMARY KEY(member, lesson, prompt_date))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS concept_feedback ("
            "concept_id TEXT NOT NULL, member TEXT NOT NULL, note TEXT NOT NULL, created REAL NOT NULL, "
            "PRIMARY KEY(concept_id, member))"
        )


def list_concepts(course: str = "", status: str = "", path: Path | None = None) -> list[dict]:
    _migrate(path)
    clauses, args = [], []
    if course:
        clauses.append("course=?")
        args.append(str(course))
    if status in {"draft", "approved"}:
        clauses.append("status=?")
        args.append(status)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with learner._conn(path) as conn:
        rows = conn.execute(
            "SELECT id, course, label, description, prereq_ids, status, source_lesson, updated "
            f"FROM concept{where} ORDER BY updated DESC LIMIT 200", args,
        ).fetchall()
    return [{**dict(row), "prereq_ids": json.loads(row["prereq_ids"] or "[]")} for row in rows]


def concept_course(concept_id: str, path: Path | None = None) -> str | None:
    with learner._conn(path) as conn:
        row = conn.execute("SELECT course FROM concept WHERE id=?", (concept_id,)).fetchone()
    return str(row["course"] or "") if row else None


def concept_status(concept_id: str, path: Path | None = None) -> str | None:
    with learner._conn(path) as conn:
        row = conn.execute("SELECT status FROM concept WHERE id=?", (concept_id,)).fetchone()
    return str(row["status"]) if row else None


def record_concept_feedback(member: str, concept_id: str, note: str = "not-accurate",
                            path: Path | None = None) -> dict:
    _migrate(path)
    if concept_status(concept_id, path) != "approved":
        raise ValueError("Chỉ nhận phản hồi cho concept đã duyệt.")
    now = time.time()
    with learner._conn(path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO concept_feedback(concept_id,member,note,created) VALUES (?,?,?,?)",
            (concept_id, member, str(note)[:500], now),
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM concept_feedback WHERE concept_id=?", (concept_id,)
        ).fetchone()[0]
        row = conn.execute(
            "SELECT p FROM mastery WHERE member=? AND concept_id=?", (member, concept_id)
        ).fetchone()
    neutral = float(row["p"]) if row else learner.INITIAL_P
    evidence = learner.record_evidence(
        member, concept_id, "feedback_correction", neutral,
        ref_doctype="widget_feedback", ref_name=member,
        ref_modified=str(int(now)), weight=0.0, path=path,
    )
    if count >= 3:
        with learner._conn(path) as conn:
            conn.execute(
                "UPDATE concept SET status='draft',updated=? WHERE id=?", (now, concept_id)
            )
    return {**evidence, "feedback_count": count, "concept_status": "draft" if count >= 3 else "approved"}


def action_course(action_id: str, path: Path | None = None) -> str | None:
    _migrate(path)
    with learner._conn(path) as conn:
        row = conn.execute("SELECT course FROM agent_action WHERE id=?", (action_id,)).fetchone()
    return str(row["course"] or "") if row else None


def extract_concepts(llm, frappe, lesson: str, path: Path | None = None) -> list[dict]:
    doc = frappe.get_document("Course Lesson", lesson).get("data", {})
    if not doc:
        raise ValueError("Không tìm thấy bài học.")
    text = re.sub(r"<[^>]+>", " ", str(doc.get("body") or doc.get("content") or ""))
    text = re.sub(r"\s+", " ", text).strip()[:10000]
    questions: list[dict] = []
    if doc.get("quiz_id"):
        try:
            quiz = frappe.get_document("LMS Quiz", str(doc["quiz_id"])).get("data", {})
            questions = [
                {"id": str(row.get("question") or ""), "text": str(row.get("question_detail") or "")}
                for row in (quiz.get("questions") or []) if row.get("question")
            ]
        except Exception:
            questions = []
    prompt = (
        "Trích 2-8 concept học tập từ bài sau. Chỉ trả JSON array; mỗi phần tử có "
        '"label", "description", "prerequisites" (mảng label), "questions" (mảng id câu hỏi). '
        "Chỉ dùng id câu hỏi được cung cấp. Không thêm markdown.\n"
        f"Tiêu đề: {doc.get('title') or lesson}\nNội dung: {text}\n"
        f"Câu hỏi: {json.dumps(questions, ensure_ascii=False)}"
    )
    response = llm.chat([
        {"role": "system", "content": "Bạn xây concept map chính xác, ngắn gọn."},
        {"role": "user", "content": prompt},
    ])
    raw = str(response["choices"][0]["message"].get("content") or "[]").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
    try:
        suggestions = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Model không trả concept JSON hợp lệ.") from exc
    if not isinstance(suggestions, list):
        raise ValueError("Concept output phải là một danh sách.")
    created = []
    label_to_id: dict[str, str] = {}
    course = str(doc.get("course") or "")
    valid_items = [item for item in suggestions[:8] if isinstance(item, dict)]
    for item in valid_items:
        if not isinstance(item, dict) or not str(item.get("label") or "").strip():
            continue
        record = learner.upsert_concept(
            str(item["label"]), course=course,
            description=str(item.get("description") or ""), source_lesson=lesson,
            status="draft", path=path,
        )
        learner.link_lesson_concept(lesson, record["id"], path=path)
        label_to_id[str(item["label"]).casefold()] = record["id"]
        created.append({**record, "label": str(item["label"]), "course": course})
    with learner._conn(path) as conn:
        for item in valid_items:
            concept_id = label_to_id.get(str(item.get("label") or "").casefold())
            if not concept_id:
                continue
            prereqs = [
                label_to_id[label.casefold()] for label in map(str, item.get("prerequisites") or [])
                if label.casefold() in label_to_id
            ]
            conn.execute(
                "UPDATE concept SET prereq_ids=? WHERE id=?",
                (json.dumps(prereqs), concept_id),
            )
            for question in item.get("questions") or []:
                if str(question) in {row["id"] for row in questions}:
                    conn.execute(
                        "INSERT OR IGNORE INTO question_concept(question,concept_id) VALUES (?,?)",
                        (str(question), concept_id),
                    )
    return created


def evaluate_session(llm, member: str, mode: str, concept_id: str, transcript: str,
                     path: Path | None = None) -> dict:
    if mode not in {"feynman", "check"}:
        raise ValueError("mode phải là feynman hoặc check.")
    prompt = (
        "Chấm transcript học tập theo 4 tiêu chí: correctness, completeness, example, limits; "
        "mỗi điểm 0..1. Trả JSON object có bốn điểm và feedback ngắn.\n"
        f"Concept: {concept_id}\nTranscript:\n{transcript[:12000]}"
    )
    response = llm.chat([
        {"role": "system", "content": "Bạn là giám khảo giáo dục nhất quán. Chỉ trả JSON."},
        {"role": "user", "content": prompt},
    ])
    raw = str(response["choices"][0]["message"].get("content") or "{}").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
    try:
        rubric = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Model không trả rubric JSON hợp lệ.") from exc
    keys = ("correctness", "completeness", "example", "limits")
    scores = [max(0.0, min(1.0, float(rubric.get(key, 0)))) for key in keys]
    score = sum(scores) / len(scores)
    saved = learner.record_learning_session(
        member, mode=mode, concept_id=concept_id, transcript=transcript,
        rubric_score=score, path=path,
    )
    return {"session": saved["id"], "score": score, "rubric": rubric}


def soft_gate(member: str, lesson: str, path: Path | None = None) -> dict | None:
    concepts = learner.concepts_for_lesson(lesson, path)
    prereqs: set[str] = set()
    with learner._conn(path) as conn:
        for concept in concepts:
            row = conn.execute("SELECT prereq_ids FROM concept WHERE id=?", (concept["id"],)).fetchone()
            if row:
                prereqs.update(json.loads(row["prereq_ids"] or "[]"))
        if not prereqs:
            return None
        marks = ",".join("?" for _ in prereqs)
        rows = conn.execute(
            f"SELECT m.concept_id,m.p,c.label FROM mastery m JOIN concept c ON c.id=m.concept_id "
            f"WHERE m.member=? AND m.concept_id IN ({marks}) AND m.p<0.4 ORDER BY m.p",
            [member, *prereqs],
        ).fetchall()
    if not rows:
        return None
    return {"lesson": lesson, "weak_prerequisites": [dict(row) for row in rows], "minutes": 3}


def mastery_with_evidence(member: str, course: str = "", path: Path | None = None) -> list[dict]:
    return learner.weak_concepts(member, course, limit=5, path=path)


def save_daily_plan(member: str, plan_date: str, path: Path | None = None) -> dict:
    _migrate(path)
    weak = learner.weak_concepts(member, limit=3, path=path)
    if weak:
        labels = ", ".join(item["label"] for item in weak)
        content = f"15 phút hôm nay: ôn {labels}; tự giải thích 5 phút và làm kiểm tra hội thoại 10 phút."
    else:
        content = "15 phút hôm nay: mở bài gần nhất, tóm tắt 5 phút và tự kiểm tra 10 phút."
    plan_id = "plan_" + uuid.uuid4().hex[:10]
    with learner._conn(path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO daily_plan(id,member,plan_date,content,created) VALUES (?,?,?,?,?)",
            (plan_id, member, plan_date, content, time.time()),
        )
        row = conn.execute(
            "SELECT * FROM daily_plan WHERE member=? AND plan_date=?", (member, plan_date)
        ).fetchone()
    return dict(row)


def plans_for(member: str, path: Path | None = None) -> list[dict]:
    _migrate(path)
    with learner._conn(path) as conn:
        rows = conn.execute(
            "SELECT * FROM daily_plan WHERE member=? ORDER BY plan_date DESC LIMIT 30", (member,)
        ).fetchall()
    return [dict(row) for row in rows]


def detect_risk(course: str = "", path: Path | None = None) -> list[dict]:
    _migrate(path)
    cutoff = time.time() - 7 * 86400
    with learner._conn(path) as conn:
        course_filter = " WHERE c.course=?" if course else ""
        args = ([course] if course else []) + [cutoff]
        inactive = conn.execute(
            "SELECT e.member,MAX(e.observed_at) last_activity FROM evidence e "
            "JOIN concept c ON c.id=e.concept_id"
            f"{course_filter} GROUP BY e.member HAVING MAX(e.observed_at)<?", args,
        ).fetchall()
        fail_args = ([course] if course else [])
        failures = conn.execute(
            "SELECT e.member,COUNT(*) failures FROM evidence e JOIN concept c ON c.id=e.concept_id "
            "WHERE e.kind='quiz_item' AND e.outcome<0.5"
            + (" AND c.course=?" if course else "")
            + " GROUP BY e.member HAVING COUNT(*)>=2", fail_args,
        ).fetchall()
    merged: dict[str, dict] = {}
    for row in inactive:
        merged[row["member"]] = {"member": row["member"], "reasons": ["Không có hoạt động trong 7 ngày"]}
    for row in failures:
        merged.setdefault(row["member"], {"member": row["member"], "reasons": []})["reasons"].append(
            f"Trượt ít nhất {row['failures']} câu quiz"
        )
    return list(merged.values())


def propose_teacher_action(course: str, target: str, subject: str, message: str,
                           actor: str, path: Path | None = None) -> dict:
    _migrate(path)
    action_id = "act_" + uuid.uuid4().hex[:12]
    payload = {"subject": subject[:180], "message": message[:2000], "link": "/ai/plan"}
    with learner._conn(path) as conn:
        conn.execute(
            "INSERT INTO agent_action(id,actor,kind,target,course,payload,status,created) "
            "VALUES (?,?,?,?,?,?,'proposed',?)",
            (action_id, actor, "teacher_message", target, course,
             json.dumps(payload, ensure_ascii=False), time.time()),
        )
    return {"id": action_id, "status": "proposed", "target": target, "payload": payload}


def approve_teacher_action(action_id: str, approved_by: str, frappe,
                           path: Path | None = None) -> dict:
    _migrate(path)
    with learner._conn(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM agent_action WHERE id=? AND status='proposed'", (action_id,)
        ).fetchone()
        if not row:
            raise ValueError("Hành động không tồn tại hoặc đã được dùng.")
        updated = conn.execute(
            "UPDATE agent_action SET status='approved',approved_by=?,approved_at=? "
            "WHERE id=? AND status='proposed'", (approved_by, time.time(), action_id),
        )
        if updated.rowcount != 1:
            raise ValueError("Hành động đã được xử lý.")
        record = dict(row)
    payload = json.loads(record["payload"])
    result = frappe.create_document("Notification Log", {
        "type": "Alert", "for_user": record["target"], "from_user": approved_by,
        "subject": payload["subject"], "email_content": payload["message"],
        "link": payload.get("link") or "/ai/plan",
    })
    with learner._conn(path) as conn:
        conn.execute(
            "UPDATE agent_action SET status='executed',executed_at=?,result=? WHERE id=?",
            (time.time(), json.dumps(result, ensure_ascii=False, default=str)[:4000], action_id),
        )
    return {"id": action_id, "status": "executed", "result": result}


def erase_member(member: str, path: Path | None = None) -> dict:
    _migrate(path)
    deleted = 0
    with learner._conn(path) as conn:
        for table, column in (
            ("evidence", "member"), ("mastery", "member"), ("learning_session", "member"),
            ("member_pref", "member"), ("daily_plan", "member"), ("route_prompt", "member"),
            ("agent_action", "target"), ("long_memories", "user"), ("concept_feedback", "member"),
        ):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists:
                deleted += conn.execute(f"DELETE FROM {table} WHERE {column}=?", (member,)).rowcount
    if path is None:
        from gateway.runtime.trace import erase_member_trace
        erase_member_trace(member)
    return {"member": member, "deleted_rows": deleted}


def purge_old_transcripts(retention_days: int = 90, path: Path | None = None) -> int:
    cutoff = time.time() - max(1, int(retention_days)) * 86400
    with learner._conn(path) as conn:
        changed = conn.execute(
            "UPDATE learning_session SET transcript='[expired]' "
            "WHERE created<? AND transcript!='[expired]'", (cutoff,)
        ).rowcount
    return changed
