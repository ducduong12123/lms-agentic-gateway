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
            "CREATE TABLE IF NOT EXISTS review_sets ("
            "id TEXT PRIMARY KEY, member TEXT NOT NULL, course TEXT NOT NULL DEFAULT '', "
            "concepts_json TEXT NOT NULL, items_json TEXT NOT NULL, created REAL NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'open')"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS scheduled_reviews ("
            "id TEXT PRIMARY KEY, member TEXT NOT NULL, concept_id TEXT NOT NULL, "
            "due_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'scheduled', created REAL NOT NULL, "
            "UNIQUE(member, concept_id, due_date))"
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
        conn.execute(
            "CREATE TABLE IF NOT EXISTS chat_session ("
            "id TEXT PRIMARY KEY, member TEXT NOT NULL, title TEXT NOT NULL DEFAULT 'New AI chat', "
            "mode TEXT NOT NULL DEFAULT 'chat', created REAL NOT NULL, updated REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS chat_message ("
            "id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL, "
            "content TEXT NOT NULL, created REAL NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_session_member "
            "ON chat_session(member, updated DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_message_session "
            "ON chat_message(session_id, created ASC)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS conversation_state ("
            "session_id TEXT PRIMARY KEY, member TEXT NOT NULL, state_json TEXT NOT NULL DEFAULT '{}', "
            "updated REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS lesson_draft ("
            "id TEXT PRIMARY KEY, session_id TEXT NOT NULL, member TEXT NOT NULL, "
            "course TEXT NOT NULL DEFAULT '', chapter TEXT NOT NULL DEFAULT '', "
            "lesson TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, body TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'draft', created REAL NOT NULL, updated REAL NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lesson_draft_session "
            "ON lesson_draft(session_id, updated DESC)"
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
    if mode not in {"feynman", "viva", "check"}:
        raise ValueError("mode phải là feynman, viva hoặc check.")
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
def create_review_set(member: str, course: str, concepts: list[dict], items: list[dict],
                      path: Path | None = None) -> dict:
    _migrate(path)
    set_id = "rs_" + uuid.uuid4().hex[:10]
    with learner._conn(path) as conn:
        conn.execute(
            "INSERT INTO review_sets(id,member,course,concepts_json,items_json,created,status) "
            "VALUES(?,?,?,?,?,?, 'open')",
            (set_id, member, course or "", json.dumps(concepts, ensure_ascii=False),
             json.dumps(items, ensure_ascii=False), time.time()),
        )
    return get_review_set(set_id, member, path) or {"id": set_id, "member": member}


def get_review_set(set_id: str, member: str, path: Path | None = None) -> dict | None:
    _migrate(path)
    with learner._conn(path) as conn:
        row = conn.execute(
            "SELECT * FROM review_sets WHERE id=? AND member=?", (set_id, member)
        ).fetchone()
    if not row:
        return None
    out = dict(row)
    out["concepts"] = json.loads(out.pop("concepts_json") or "[]")
    out["items"] = json.loads(out.pop("items_json") or "[]")
    return out
def find_open_review_set(member: str, course: str, concept_ids: list[str], path: Path | None = None) -> dict | None:
    _migrate(path)
    wanted = sorted(str(value) for value in concept_ids)
    with learner._conn(path) as conn:
        rows = conn.execute(
            "SELECT * FROM review_sets WHERE member=? AND course=? AND status='open' ORDER BY created DESC LIMIT 20",
            (member, course or ""),
        ).fetchall()
    for row in rows:
        item = json.loads(row["concepts_json"] or "[]")
        actual = sorted(str(value.get("id") or value.get("concept_id") or "") for value in item if isinstance(value, dict))
        if actual == wanted:
            out = dict(row)
            out["concepts"] = json.loads(out.pop("concepts_json") or "[]")
            out["items"] = json.loads(out.pop("items_json") or "[]")
            return out
    return None


def delete_review_set(set_id: str, member: str | None = None, path: Path | None = None) -> bool:
    _migrate(path)
    with learner._conn(path) as conn:
        if member is None:
            changed = conn.execute("DELETE FROM review_sets WHERE id=?", (set_id,)).rowcount
        else:
            changed = conn.execute(
                "DELETE FROM review_sets WHERE id=? AND member=?", (set_id, member)
            ).rowcount
    return changed == 1


def answer_review_item(set_id: str, item_id: str, answer: str, member: str,
                       path: Path | None = None) -> dict:
    review = get_review_set(set_id, member, path)
    if not review:
        raise ValueError("review set not found")
    item = next((item for item in review["items"] if str(item.get("id")) == str(item_id)), None)
    if not item:
        raise ValueError("review item not found")
    text = str(answer or "").strip()
    expected = str(item.get("expected") or "").strip().casefold()
    correct = bool(text) and (not expected or expected in text.casefold())
    score = 1.0 if correct else 0.0
    concept_id = str(item.get("concept_id") or "")
    if concept_id:
        learner.record_evidence(
            member=member,
            concept_id=concept_id,
            kind="review_item",
            outcome=score,
            ref_doctype="GatewayReviewSet",
            ref_name=f"{set_id}:{item_id}",
            path=path,
        )
    mastery_after = next(
        (row.get("p") for row in learner.mastery_snapshot(member, path) if row.get("concept_id") == concept_id),
        None,
    ) if concept_id else None
    return {
        "correct": correct,
        "explanation": item.get("explanation") or ("Đúng." if correct else "Hãy xem lại concept này và thử lại."),
        "mastery_after": mastery_after,
    }


def schedule_review(member: str, concept_id: str, due_date: str, path: Path | None = None) -> dict:
    _migrate(path)
    review_id = "rev_" + uuid.uuid4().hex[:10]
    with learner._conn(path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO scheduled_reviews(id,member,concept_id,due_date,created) "
            "VALUES(?,?,?,?,?)",
            (review_id, member, concept_id, due_date, time.time()),
        )
        row = conn.execute(
            "SELECT * FROM scheduled_reviews WHERE member=? AND concept_id=? AND due_date=?",
            (member, concept_id, due_date),
        ).fetchone()
    return dict(row)


def cancel_scheduled_review(review_id: str, member: str | None = None, path: Path | None = None) -> bool:
    _migrate(path)
    with learner._conn(path) as conn:
        if member is None:
            changed = conn.execute("DELETE FROM scheduled_reviews WHERE id=?", (review_id,)).rowcount
        else:
            changed = conn.execute(
                "DELETE FROM scheduled_reviews WHERE id=? AND member=?", (review_id, member)
            ).rowcount
    return changed == 1


def scheduled_reviews_for(member: str, path: Path | None = None) -> list[dict]:
    _migrate(path)
    with learner._conn(path) as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_reviews WHERE member=? AND status='scheduled' ORDER BY due_date",
            (member,),
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

def risk_overview(course: str = "", limit: int = 10, path: Path | None = None) -> list[dict]:
    """Học viên nguy cơ kèm concept yếu đã duyệt, yếu nhất trước. Nguồn cho tool teacher/admin."""
    try:
        limit = max(1, min(50, int(limit or 10)))
    except (TypeError, ValueError):
        limit = 10
    course = str(course or "").strip()
    out: list[dict] = []
    for row in detect_risk(course, path):
        member = str(row.get("member") or "")
        try:
            weak = learner.weak_concepts(member, course, limit=3, path=path)
        except Exception:
            weak = []
        scores = [float(item.get("p", 1.0)) for item in weak if isinstance(item, dict)]
        out.append(
            {
                "member": member,
                "reasons": list(row.get("reasons") or []),
                "weakest_p": min(scores) if scores else None,
                "weak_concepts": weak,
            }
        )
    out.sort(
        key=lambda item: (
            item["weakest_p"] is None,
            item["weakest_p"] if item["weakest_p"] is not None else 0.0,
        )
    )
    return out[:limit]


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
        session_ids = [
            row["id"] for row in conn.execute(
                "SELECT id FROM chat_session WHERE member=?", (member,)
            ).fetchall()
        ]
        for table, column in (
            ("evidence", "member"), ("mastery", "member"), ("learning_session", "member"),
            ("member_pref", "member"), ("daily_plan", "member"), ("route_prompt", "member"),
            ("agent_action", "target"), ("long_memories", "user"), ("concept_feedback", "member"),
            ("conversation_state", "member"), ("lesson_draft", "member"),
            ("course_project", "member"), ("chat_session", "member"),
        ):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists:
                deleted += conn.execute(f"DELETE FROM {table} WHERE {column}=?", (member,)).rowcount
        for session_id in session_ids:
            deleted += conn.execute(
                "DELETE FROM chat_message WHERE session_id=?", (session_id,)
            ).rowcount
            conn.execute("DELETE FROM conversation_state WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM lesson_draft WHERE session_id=?", (session_id,))
    if path is None:
        from gateway.runtime.trace import erase_member_trace
        erase_member_trace(member)
    return {"member": member, "deleted_rows": deleted}


def _session_title(text: str) -> str:
    title = " ".join(str(text or "").split())[:60] or "New AI chat"
    return title


def ensure_chat_session(
    member: str, session_id: str = "", title: str = "",
    mode: str = "chat", path: Path | None = None,
) -> dict:
    """Lấy hoặc tạo session chat của đúng user. Session rỗng thì tạo mới."""
    _migrate(path)
    member = str(member or "").strip()
    if not member or member == "Guest":
        raise ValueError("member chưa đăng nhập.")
    session_id = str(session_id or "").strip()[:128]
    mode = str(mode or "chat").strip()[:16] or "chat"
    now = time.time()
    with learner._conn(path) as conn:
        if session_id:
            row = conn.execute(
                "SELECT * FROM chat_session WHERE id=? AND member=?", (session_id, member)
            ).fetchone()
            if row:
                return dict(row)
        session_id = "cht_" + uuid.uuid4().hex[:10]
        conn.execute(
            "INSERT INTO chat_session(id, member, title, mode, created, updated)"
            " VALUES (?,?,?,?,?,?)",
            (session_id, member, _session_title(title), mode, now, now),
        )
        return {
            "id": session_id, "member": member,
            "title": _session_title(title), "mode": mode,
            "created": now, "updated": now,
        }

def get_conversation_state(
    member: str, session_id: str, path: Path | None = None,
) -> dict:
    _migrate(path)
    member = str(member or "").strip()
    session_id = str(session_id or "").strip()
    if not member or member == "Guest" or not session_id:
        return {}
    with learner._conn(path) as conn:
        row = conn.execute(
            "SELECT state_json,updated FROM conversation_state WHERE session_id=? AND member=?",
            (session_id, member),
        ).fetchone()
    if not row:
        return {}
    try:
        state = json.loads(row["state_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    state["updated"] = row["updated"]
    return state


def update_conversation_state(
    member: str, session_id: str, changes: dict, path: Path | None = None,
) -> dict:
    _migrate(path)
    member = str(member or "").strip()
    session_id = str(session_id or "").strip()
    if not member or member == "Guest" or not session_id:
        raise ValueError("thiếu member hoặc session.")
    clean = {
        str(key): value for key, value in dict(changes or {}).items()
        if value is not None and str(key) != "updated"
    }
    now = time.time()
    with learner._conn(path) as conn:
        owner = conn.execute(
            "SELECT 1 FROM chat_session WHERE id=? AND member=?", (session_id, member)
        ).fetchone()
        if not owner:
            raise ValueError("session không thuộc user.")
        row = conn.execute(
            "SELECT state_json FROM conversation_state WHERE session_id=? AND member=?",
            (session_id, member),
        ).fetchone()
        try:
            state = json.loads(row["state_json"] or "{}") if row else {}
        except (TypeError, json.JSONDecodeError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        state.update(clean)
        conn.execute(
            "INSERT INTO conversation_state(session_id,member,state_json,updated) VALUES(?,?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET member=excluded.member,"
            "state_json=excluded.state_json,updated=excluded.updated",
            (session_id, member, json.dumps(state, ensure_ascii=False), now),
        )
    return {**state, "updated": now}


def get_lesson_draft(
    member: str, draft_id: str, path: Path | None = None,
) -> dict | None:
    _migrate(path)
    with learner._conn(path) as conn:
        row = conn.execute(
            "SELECT * FROM lesson_draft WHERE id=? AND member=?",
            (str(draft_id or ""), str(member or "")),
        ).fetchone()
    return dict(row) if row else None

def mark_lesson_draft_status(
    member: str, draft_id: str, status: str, path: Path | None = None,
) -> bool:
    if status not in {"draft", "pending_approval", "published", "superseded"}:
        raise ValueError("trạng thái draft không hợp lệ.")
    with learner._conn(path) as conn:
        changed = conn.execute(
            "UPDATE lesson_draft SET status=?,updated=? WHERE id=? AND member=?",
            (status, time.time(), str(draft_id or ""), str(member or "")),
        ).rowcount
    return changed == 1


def save_lesson_draft(
    member: str, session_id: str, title: str, body: str,
    course: str = "", chapter: str = "", lesson: str = "",
    path: Path | None = None,
) -> dict:
    member = str(member or "").strip()
    session_id = str(session_id or "").strip()
    title = str(title or "").strip()[:180]
    body = str(body or "").strip()
    if not title or not body:
        raise ValueError("title và body là bắt buộc.")
    state = get_conversation_state(member, session_id, path)
    existing_id = str(state.get("active_draft_id") or "")
    existing = get_lesson_draft(member, existing_id, path) if existing_id else None
    now = time.time()
    draft_id = existing_id if existing and existing.get("status") == "draft" else "draft_" + uuid.uuid4().hex[:12]
    created = float(existing.get("created") or now) if existing else now
    with learner._conn(path) as conn:
        owner = conn.execute(
            "SELECT 1 FROM chat_session WHERE id=? AND member=?", (session_id, member)
        ).fetchone()
        if not owner:
            raise ValueError("session không thuộc user.")
        conn.execute(
            "INSERT OR REPLACE INTO lesson_draft"
            "(id,session_id,member,course,chapter,lesson,title,body,status,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                draft_id, session_id, member, str(course or ""), str(chapter or ""),
                str(lesson or ""), title, body, "draft", created, now,
            ),
        )
    update_conversation_state(member, session_id, {"active_draft_id": draft_id}, path)
    return get_lesson_draft(member, draft_id, path) or {"id": draft_id}


def _last_context_value(text: str, label: str) -> str:
    matches = re.findall(
        rf"(?im)(?:\*\*)?{re.escape(label)}(?:\*\*)?\s*:\s*([^,\n`]+)",
        text,
    )
    ignored = {"", "chưa xác định", "không xác định", "none", "null", "toàn bộ dữ liệu"}
    for value in reversed(matches):
        cleaned = value.strip(" .*")
        if cleaned.casefold() not in ignored:
            return cleaned
    return ""


def capture_conversation_artifacts(
    member: str, session_id: str, user_message: str, assistant_message: str,
    path: Path | None = None,
) -> dict:
    """Persist deterministic LMS workflow facts and substantial lesson drafts."""
    combined = f"{user_message}\n{assistant_message}"
    state = get_conversation_state(member, session_id, path)
    changes: dict = {}
    course = _last_context_value(combined, "LMS Course")
    chapter = _last_context_value(combined, "Chapter") or _last_context_value(combined, "Chương")
    lesson = _last_context_value(combined, "Course Lesson")
    if course:
        changes["active_course"] = course
    if chapter:
        changes["active_chapter"] = chapter
    if lesson:
        changes["active_lesson"] = lesson
    if changes:
        state = update_conversation_state(member, session_id, changes, path)

    heading = re.search(r"(?m)^#{1,3}\s+(.+?)\s*$", str(assistant_message or ""))
    drafting = any(
        phrase in str(user_message or "").casefold()
        for phrase in ("soạn", "soan", "viết bài", "viet bai", "tạo bài", "tao bai")
    )
    if drafting and heading and len(str(assistant_message or "").strip()) >= 300:
        body = str(assistant_message or "").strip()
        source_at = re.search(r"(?m)^\s*(?:Nguồn:\s*)?LMS Course\s*:", body)
        if source_at:
            body = body[:source_at.start()].rstrip()
        draft = save_lesson_draft(
            member, session_id, heading.group(1).strip(), body,
            course=str(state.get("active_course") or ""),
            chapter=str(state.get("active_chapter") or ""),
            lesson="",
            path=path,
        )
        state["active_draft_id"] = draft["id"]
    return state


def workflow_context(
    member: str, session_id: str, path: Path | None = None,
) -> dict:
    state = get_conversation_state(member, session_id, path)
    draft_id = str(state.get("active_draft_id") or "")
    draft = get_lesson_draft(member, draft_id, path) if draft_id else None
    if draft:
        state["active_draft"] = draft
    from gateway.runtime.course_projects import active_project
    project = active_project(member, session_id, path)
    if project:
        state["active_project"] = project
    return state


def append_chat_turn(
    member: str, session_id: str, user_message: str, assistant_message: str,
    path: Path | None = None,
) -> dict:
    _migrate(path)
    member = str(member or "").strip()
    session_id = str(session_id or "").strip()
    if not member or member == "Guest" or not session_id:
        raise ValueError("thiếu member hoặc session.")
    user_message = str(user_message or "").strip()
    assistant_message = str(assistant_message or "").strip()
    if not user_message and not assistant_message:
        return {"session_id": session_id, "saved": 0}
    now = time.time()
    with learner._conn(path) as conn:
        owner = conn.execute(
            "SELECT id, title FROM chat_session WHERE id=? AND member=?",
            (session_id, member),
        ).fetchone()
        if not owner:
            raise ValueError("session không thuộc user.")
        saved = 0
        if user_message:
            conn.execute(
                "INSERT INTO chat_message(id, session_id, role, content, created)"
                " VALUES (?,?,?,?,?)",
                ("msg_" + uuid.uuid4().hex[:10], session_id, "user",
                 user_message[:12000], now),
            )
            saved += 1
        if assistant_message:
            conn.execute(
                "INSERT INTO chat_message(id, session_id, role, content, created)"
                " VALUES (?,?,?,?,?)",
                ("msg_" + uuid.uuid4().hex[:10], session_id, "assistant",
                 assistant_message[:12000], now + 0.001),
            )
            saved += 1
        title = str(dict(owner).get("title") or "")
        if title in ("", "New AI chat") and user_message:
            title = _session_title(user_message)
        conn.execute(
            "UPDATE chat_session SET updated=?, title=? WHERE id=?",
            (time.time(), title[:60], session_id),
        )
    capture_conversation_artifacts(
        member, session_id, user_message, assistant_message, path,
    )
    try:
        from gateway.runtime.trace import trace as _trace
        _trace(
            "session", "turn", member=member, session_id=session_id,
            user_msg=user_message, assistant_msg=assistant_message,
        )
    except Exception:
        pass
    return {"session_id": session_id, "saved": saved}


def list_chat_sessions(member: str, limit: int = 30, path: Path | None = None) -> list[dict]:
    _migrate(path)
    member = str(member or "").strip()
    if not member or member == "Guest":
        return []
    limit = max(1, min(100, int(limit or 30)))
    with learner._conn(path) as conn:
        rows = conn.execute(
            "SELECT s.id, s.title, s.mode, s.created, s.updated,"
            " (SELECT COUNT(*) FROM chat_message m WHERE m.session_id=s.id) AS turns,"
            " (SELECT content FROM chat_message m WHERE m.session_id=s.id AND role='user'"
            "  ORDER BY created ASC LIMIT 1) AS first_msg"
            " FROM chat_session s WHERE s.member=? ORDER BY s.updated DESC LIMIT ?",
            (member, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_chat_session(member: str, session_id: str, limit: int = 200, path: Path | None = None) -> dict | None:
    _migrate(path)
    member = str(member or "").strip()
    session_id = str(session_id or "").strip()
    if not member or member == "Guest" or not session_id:
        return None
    limit = max(1, min(500, int(limit or 200)))
    with learner._conn(path) as conn:
        session = conn.execute(
            "SELECT * FROM chat_session WHERE id=? AND member=?", (session_id, member)
        ).fetchone()
        if not session:
            return None
        messages = conn.execute(
            "SELECT role,content,created FROM ("
            " SELECT role,content,created FROM chat_message"
            " WHERE session_id=? ORDER BY created DESC LIMIT ?"
            ") ORDER BY created ASC",
            (session_id, limit),
        ).fetchall()
    return {"session": dict(session), "messages": [dict(row) for row in messages]}


def rename_chat_session(member: str, session_id: str, title: str, path: Path | None = None) -> bool:
    _migrate(path)
    member = str(member or "").strip()
    title = _session_title(title)
    if not member or member == "Guest" or not session_id:
        return False
    with learner._conn(path) as conn:
        changed = conn.execute(
            "UPDATE chat_session SET title=?, updated=? WHERE id=? AND member=?",
            (title, time.time(), str(session_id), member),
        ).rowcount
    return changed > 0


def delete_chat_session(member: str, session_id: str, path: Path | None = None) -> bool:
    _migrate(path)
    member = str(member or "").strip()
    session_id = str(session_id or "").strip()
    if not member or member == "Guest" or not session_id:
        return False
    with learner._conn(path) as conn:
        conn.execute("DELETE FROM chat_message WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM conversation_state WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM lesson_draft WHERE session_id=?", (session_id,))
        changed = conn.execute(
            "DELETE FROM chat_session WHERE id=? AND member=?", (session_id, member)
        ).rowcount
    try:
        from gateway.runtime.trace import trace as _trace
        _trace("session", "delete", member=member, session_id=session_id, ok=changed > 0)
    except Exception:
        pass
    return changed > 0


def purge_old_transcripts(retention_days: int = 90, path: Path | None = None) -> int:
    cutoff = time.time() - max(1, int(retention_days)) * 86400
    with learner._conn(path) as conn:
        changed = conn.execute(
            "UPDATE learning_session SET transcript='[expired]' "
            "WHERE created<? AND transcript!='[expired]'", (cutoff,)
        ).rowcount
    return changed
