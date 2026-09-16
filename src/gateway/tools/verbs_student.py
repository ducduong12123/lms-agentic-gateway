"""Self-scoped student verbs with idempotency and bounded undo metadata."""
from __future__ import annotations

from datetime import date
import json
import re

from gateway.runtime import features, learner
from gateway.runtime.tool_registry import Tool, ToolRegistry
from gateway.tools.envelope import action_result


def _change(doctype: str, name: str, op: str) -> dict:
    return {"doctype": doctype, "name": str(name), "op": op}


def _llm_review_items(llm, concepts: list[dict], size: int) -> list[dict]:
    if not llm or not concepts:
        return []
    context = [
        {"id": str(item.get("id") or ""), "label": str(item.get("label") or ""),
         "description": str(item.get("description") or "")}
        for item in concepts[:size]
    ]
    response = llm.chat([
        {"role": "system", "content": "Bạn tạo câu hỏi ôn tập ngắn, chính xác. Chỉ trả JSON array, không markdown."},
        {"role": "user", "content": (
            "Tạo một câu hỏi tự luận cho mỗi concept sau. Mỗi phần tử phải có "
            '"concept_id", "prompt", "expected", "explanation". Chỉ dùng concept_id được cung cấp. '
            f"{json.dumps(context, ensure_ascii=False)}"
        )},
    ])
    raw = str(response["choices"][0]["message"].get("content") or "[]").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
    try:
        generated = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Model không trả câu hỏi ôn tập JSON hợp lệ.") from exc
    if not isinstance(generated, list):
        raise ValueError("Review output phải là một danh sách.")
    valid_ids = {item["id"] for item in context if item["id"]}
    items = []
    for index, item in enumerate(generated[:size], start=1):
        if not isinstance(item, dict):
            continue
        concept_id = str(item.get("concept_id") or "")
        prompt = str(item.get("prompt") or "").strip()
        if concept_id not in valid_ids or not prompt:
            continue
        items.append({
            "id": f"item_{index}",
            "concept_id": concept_id,
            "prompt": prompt[:2000],
            "expected": str(item.get("expected") or "")[:2000],
            "explanation": str(item.get("explanation") or "")[:2000],
        })
    if not items:
        raise ValueError("Model không tạo được câu hỏi ôn tập hợp lệ.")
    return items


def register(reg: ToolRegistry, frappe, llm=None) -> None:
    def enroll_course(a: dict):
        member, course = str(a.get("member") or ""), str(a.get("course") or "").strip()
        if not course:
            raise ValueError("course is required")
        rows = frappe.list_documents(
            "LMS Enrollment", filters={"member": member, "course": course}, fields=["name", "role"], limit=1
        ).get("data", [])
        if rows:
            existing = rows[0]
            return action_result(
                "enroll_course", "Bạn đã có trong khóa học", f"Enrollment {course} đã tồn tại.",
                changes=[_change("LMS Enrollment", existing.get("name", course), "existing")],
                status="noop", view={"type": "course", "course": course},
            )
        created = frappe.create_document("LMS Enrollment", {
            "member": member, "course": course, "member_type": "Student", "role": "Member",
        }).get("data", {})
        name = created.get("name") or course
        return action_result(
            "enroll_course", "Đã ghi danh khóa học", f"Bạn đã được ghi danh vào {course}.",
            changes=[_change("LMS Enrollment", name, "create")],
            undo={"op": "delete", "doctype": "LMS Enrollment", "name": name},
            view={"type": "course", "course": course},
        )

    reg.register(Tool(
        "enroll_course", "Ghi danh chính người học vào một khóa học; gọi lại an toàn.",
        {"type": "object", "properties": {"course": {"type": "string"}}, "required": ["course"]},
        enroll_course,
    ))

    def mark_lesson_complete(a: dict):
        member = str(a.get("member") or "")
        course = str(a.get("course") or "").strip()
        chapter = str(a.get("chapter") or "").strip()
        lesson = str(a.get("lesson") or "").strip()
        if not course or not chapter or not lesson:
            raise ValueError("course, chapter and lesson are required")
        chapter_number = int(a.get("chapter_number") or (chapter if chapter.isdigit() else 1))
        rows = frappe.list_documents(
            "LMS Course Progress", filters={"member": member, "course": course, "lesson": lesson},
            fields=["name", "status"], limit=1,
        ).get("data", [])
        if rows and str(rows[0].get("status")) == "Complete":
            return action_result(
                "mark_lesson_complete", "Bài học đã hoàn tất", f"Bài {lesson} đã được đánh dấu hoàn tất.",
                changes=[_change("LMS Course Progress", rows[0].get("name", lesson), "existing")],
                status="noop", view={"type": "lesson", "course": course, "chapter": chapter_number, "lesson_number": int(lesson) if lesson.isdigit() else 1},
            )
        if rows:
            name, before = rows[0].get("name", lesson), rows[0].get("status", "Incomplete")
            frappe.update_document("LMS Course Progress", name, {"status": "Complete"})
            undo = {"op": "restore", "doctype": "LMS Course Progress", "name": name, "fields": {"status": before}}
            op = "update"
        else:
            created = frappe.create_document("LMS Course Progress", {
                "course": course, "chapter": chapter, "lesson": lesson, "member": member, "status": "Complete",
            }).get("data", {})
            name = created.get("name") or lesson
            undo = {"op": "delete", "doctype": "LMS Course Progress", "name": name}
            op = "create"
        lesson_number = int(lesson) if lesson.isdigit() else int(a.get("lesson_number") or 1)
        next_lesson = lesson_number + 1 if lesson.isdigit() else int(a.get("next_lesson") or lesson_number)
        return action_result(
            "mark_lesson_complete", "Đã hoàn thành bài học", f"Bài {lesson} đã được ghi nhận.",
            changes=[_change("LMS Course Progress", name, op)], undo=undo,
            view={"type": "lesson", "course": course, "chapter": chapter_number, "lesson_number": next_lesson},
        )

    reg.register(Tool(
        "mark_lesson_complete", "Đánh dấu bài học hiện tại hoàn tất cho chính người học.",
        {"type": "object", "properties": {"course": {"type": "string"}, "chapter": {"type": "string"}, "chapter_number": {"type": "integer"}, "lesson": {"type": "string"}, "lesson_number": {"type": "integer"}, "next_lesson": {"type": "integer"}}, "required": ["course", "chapter", "lesson"]},
        mark_lesson_complete,
    ))

    def save_note(a: dict):
        member, course, lesson, note = (str(a.get(key) or "").strip() for key in ("member", "course", "lesson", "note"))
        if not lesson or not note:
            raise ValueError("lesson and note are required")
        color = str(a.get("color") or "Blue")
        if color not in {"Red", "Blue", "Green", "Yellow", "Purple"}:
            raise ValueError("unsupported note color")
        created = frappe.create_document("LMS Lesson Note", {
            "lesson": lesson, "course": course, "member": member, "note": note,
            "highlighted_text": str(a.get("highlighted_text") or ""), "color": color,
        }).get("data", {})
        name = created.get("name") or lesson
        return action_result(
            "save_note", "Đã lưu ghi chú", "Ghi chú đã được lưu vào bài học.",
            changes=[_change("LMS Lesson Note", name, "create")],
            undo={"op": "delete", "doctype": "LMS Lesson Note", "name": name},
            view={"type": "lesson", "course": course, "chapter": int(a.get("chapter") or 1), "lesson_number": int(a.get("lesson_number") or 1)},
        )

    reg.register(Tool(
        "save_note", "Lưu ghi chú cá nhân vào bài học hiện tại.",
        {"type": "object", "properties": {"lesson": {"type": "string"}, "course": {"type": "string"}, "note": {"type": "string"}, "highlighted_text": {"type": "string"}, "color": {"type": "string"}}, "required": ["lesson", "note"]},
        save_note,
    ))

    def create_review_set(a: dict):
        member, course = str(a.get("member") or ""), str(a.get("course") or "")
        size = max(1, min(int(a.get("size") or 5), 20))
        concepts = learner.weak_concepts(member, course, limit=size)
        items = _llm_review_items(llm, concepts, size)
        if not items:
            items = [
                {
                    "id": f"item_{idx + 1}",
                    "concept_id": c["id"],
                    "prompt": f"Hãy giải thích concept: {c.get('label') or c['id']}",
                    "expected": str(c.get("label") or ""),
                    "explanation": c.get("description") or "Ôn lại concept này trong bài học.",
                }
                for idx, c in enumerate(concepts)
            ]
        if not items:
            items = [{"id": "item_1", "concept_id": "", "prompt": "Hãy tóm tắt bài học hiện tại bằng ba ý.", "expected": "", "explanation": "Dùng ví dụ cụ thể để tự kiểm tra."}]
        existing = features.find_open_review_set(member, course, [item["concept_id"] for item in items])
        if existing:
            return action_result(
                "create_review_set", "Bộ ôn tập đã có sẵn", f"Bộ ôn {existing['id']} đã tồn tại.",
                status="noop", view={"type": "review_set", "set_id": existing["id"]},
            )
        review = features.create_review_set(member, course, concepts, items)
        return action_result(
            "create_review_set", "Đã tạo bộ ôn tập", f"Bộ ôn gồm {len(items)} câu hỏi.",
            undo={"op": "delete_review_set", "set_id": review["id"]},
            view={"type": "review_set", "set_id": review["id"]},
        )
    reg.register(Tool(
        "create_review_set", "Tạo bộ ôn tập từ các concept yếu nhất của chính người học.",
        {"type": "object", "properties": {"course": {"type": "string"}, "size": {"type": "integer", "default": 5}},
         "required": []}, create_review_set,
    ))

    def schedule_review(a: dict):
        member, concept_id, when = str(a.get("member") or ""), str(a.get("concept_id") or "").strip(), str(a.get("when") or "").strip()
        if not concept_id or not when:
            raise ValueError("concept_id and when are required")
        existing = next(
            (row for row in features.scheduled_reviews_for(member) if row["concept_id"] == concept_id and row["due_date"] == when),
            None,
        )
        if existing:
            return action_result(
                "schedule_review", "Lịch ôn đã có sẵn", f"Concept {concept_id} đã được lên lịch vào {when}.",
                changes=[_change("GatewayScheduledReview", existing["id"], "existing")],
                status="noop", view={"type": "plan", "date": when},
            )
        review = features.schedule_review(member, concept_id, when)
        return action_result(
            "schedule_review", "Đã lên lịch ôn tập", f"Sẽ nhắc ôn concept {concept_id} vào {when}.",
            changes=[_change("GatewayScheduledReview", review["id"], "create")],
            undo={"op": "cancel_review", "id": review["id"]}, view={"type": "plan", "date": when},
        )

    reg.register(Tool(
        "schedule_review", "Lên lịch nhắc ôn một concept vào ngày ISO YYYY-MM-DD.",
        {"type": "object", "properties": {"concept_id": {"type": "string"}, "when": {"type": "string"}}, "required": ["concept_id", "when"]}, schedule_review,
    ))

    def set_goal(a: dict):
        old = learner.get_member_pref(str(a.get("member") or ""))
        value = learner.set_member_pref(
            str(a.get("member") or ""), bool(a.get("daily_plan_opt_in", old.get("daily_plan_opt_in"))),
            str(a.get("quiet_hours", old.get("quiet_hours") or "")),
        )
        return action_result(
            "set_goal", "Đã cập nhật mục tiêu", "Tùy chọn kế hoạch học hằng ngày đã được cập nhật.",
            changes=[_change("GatewayMemberPreference", value["member"], "update")],
            undo={"op": "restore_pref", "member": value["member"], "daily_plan_opt_in": old.get("daily_plan_opt_in", False), "quiet_hours": old.get("quiet_hours", "")},
            view={"type": "plan", "date": date.today().isoformat()},
        )

    reg.register(Tool(
        "set_goal", "Cập nhật mục tiêu và nhắc kế hoạch học của chính người học.",
        {"type": "object", "properties": {"daily_plan_opt_in": {"type": "boolean"}, "quiet_hours": {"type": "string"}}, "required": []}, set_goal,
    ))

    def start_session(a: dict):
        mode, concept_id = str(a.get("mode") or "check"), str(a.get("concept_id") or "")
        if mode not in {"feynman", "viva", "check"} or not concept_id:
            raise ValueError("mode and concept_id are required")
        return action_result(
            "start_session", "Đã mở phiên học", f"Phiên {mode} đã sẵn sàng.",
            undo=None, view={"type": "session", "mode": mode, "concept_id": concept_id},
        )

    reg.register(Tool(
        "start_session", "Mở phiên Feynman, viva hoặc kiểm tra cho một concept đã duyệt.",
        {"type": "object", "properties": {"mode": {"type": "string"}, "concept_id": {"type": "string"}}, "required": ["mode", "concept_id"]}, start_session,
    ))
