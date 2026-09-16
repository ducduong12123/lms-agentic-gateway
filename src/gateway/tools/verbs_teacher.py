"""Teacher/admin verbs. Mutations are registered as approval-gated tools."""
from __future__ import annotations

from datetime import datetime

from gateway.runtime import features
from gateway.runtime.tool_registry import Tool, ToolRegistry
from gateway.tools.envelope import action_result, client_directive
from gateway.tools.verbs_course_authoring import ensure_lesson_reference


def _change(doctype: str, name: str, op: str) -> dict:
    return {"doctype": doctype, "name": str(name), "op": op}

def register(reg: ToolRegistry, frappe) -> None:
    def message_students(a: dict):
        targets = a.get("targets") or a.get("target") or []
        if isinstance(targets, str):
            targets = [targets]
        targets = [str(target).strip() for target in targets if str(target).strip()]
        subject = str(a.get("subject") or "")[:180]
        message = str(a.get("message") or "")[:2000]
        course = str(a.get("course") or a.get("batch") or "")
        member = str(a.get("member") or "")
        if not targets or not subject or not message or not member:
            raise ValueError("targets, subject, message and member are required")
        changes = []
        created_count = 0
        for target in targets:
            existing = frappe.list_documents(
                "Notification Log",
                filters={"for_user": target, "subject": subject, "email_content": message},
                fields=["name"], limit=1,
            ).get("data", [])
            if existing:
                name, op = existing[0].get("name") or target, "existing"
            else:
                proposed = features.propose_teacher_action(course, target, subject, message, member)
                executed = features.approve_teacher_action(proposed["id"], member, frappe)
                result = (executed.get("result") or {}).get("data", {})
                name, op = result.get("name") or target, "create"
                created_count += 1
            changes.append(_change("Notification Log", name, op))
        return action_result(
            "message_students",
            "Đã gửi thông báo" if created_count else "Thông báo đã có sẵn",
            f"Đã gửi tới {created_count} học viên mới; {len(targets) - created_count} thông báo đã tồn tại.",
            changes=changes, status="done" if created_count else "noop",
        )

    reg.register(Tool(
        "message_students", "Nhắn nhiều học viên; luôn cần teacher/admin phê duyệt trước.",
        {"type": "object", "properties": {"course": {"type": "string"}, "batch": {"type": "string"}, "targets": {"type": "array", "items": {"type": "string"}}, "subject": {"type": "string"}, "message": {"type": "string"}}, "required": ["targets", "subject", "message"]},
        message_students, needs_approval=True, approval_roles={"teacher", "admin"},
    ))

    def create_live_class(a: dict):
        batch = str(a.get("batch") or "").strip()
        title = str(a.get("title") or "Lớp học trực tiếp").strip()
        when = str(a.get("when") or "").strip()
        duration = max(1, min(int(a.get("duration") or 60), 480))
        timezone = str(a.get("timezone") or "Asia/Ho_Chi_Minh").strip()
        member = str(a.get("member") or "").strip()
        if not batch or not when or not member:
            raise ValueError("batch, when and member are required")
        try:
            start = datetime.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("when must be an ISO datetime") from exc
        event_date, event_time = start.date().isoformat(), start.time().replace(microsecond=0).isoformat()
        existing = frappe.list_documents(
            "LMS Live Class",
            filters={"batch_name": batch, "title": title, "date": event_date, "time": event_time},
            fields=["name"], limit=1,
        ).get("data", [])
        if existing:
            return action_result(
                "create_live_class", "Lớp trực tiếp đã có sẵn", f"Lớp {title} đã tồn tại trong batch {batch}.",
                changes=[_change("LMS Live Class", existing[0].get("name") or title, "existing")],
                status="noop",
            )
        created = frappe.create_document("LMS Live Class", {
            "batch_name": batch, "title": title, "date": event_date, "time": event_time,
            "duration": duration, "timezone": timezone, "host": member,
        }).get("data", {})
        name = created.get("name") or title
        return action_result(
            "create_live_class", "Đã tạo lớp trực tiếp", f"Lớp {title} đã được tạo cho batch {batch}.",
            changes=[_change("LMS Live Class", name, "create")],
            undo={"op": "delete", "doctype": "LMS Live Class", "name": name},
        )

    reg.register(Tool(
        "create_live_class", "Tạo lớp học trực tiếp cho một batch; cần phê duyệt.",
        {"type": "object", "properties": {"batch": {"type": "string"}, "title": {"type": "string"}, "when": {"type": "string"}, "timezone": {"type": "string", "default": "Asia/Ho_Chi_Minh"}, "duration": {"type": "integer", "default": 60}}, "required": ["batch", "title", "when"]},
        create_live_class, needs_approval=True, approval_roles={"teacher", "admin"},
    ))

    def publish_lesson_draft(a: dict):
        member = str(a.get("member") or "").strip()
        draft_id = str(a.get("draft_id") or "").strip()
        draft = features.get_lesson_draft(member, draft_id) if member and draft_id else None
        course = str(a.get("course") or (draft or {}).get("course") or "").strip()
        lesson = str(a.get("lesson") or (draft or {}).get("lesson") or "").strip()
        title = str(a.get("title") or (draft or {}).get("title") or "").strip()
        body = str(a.get("body") or (draft or {}).get("body") or "")
        chapter = str(a.get("chapter") or (draft or {}).get("chapter") or "").strip()
        if not course or not title or not body:
            raise ValueError("course, title and body are required")
        if lesson:
            before = frappe.get_document("Course Lesson", lesson).get("data", {})
            result = frappe.update_document("Course Lesson", lesson, {"title": title, "body": body})
            name, op = lesson, "update"
            undo = {"op": "restore", "doctype": "Course Lesson", "name": lesson, "fields": {"title": before.get("title", ""), "body": before.get("body", "")}}
        else:
            if not chapter:
                raise ValueError("chapter is required when creating a lesson")
            existing = frappe.list_documents(
                "Course Lesson", filters={"course": course, "chapter": chapter, "title": title},
                fields=["name"], limit=1,
            ).get("data", [])
            if existing:
                if draft_id and member:
                    features.mark_lesson_draft_status(member, draft_id, "published")
                return action_result(
                    "publish_lesson_draft", "Bản nháp đã có sẵn", f"Lesson {title} đã tồn tại trong {course}.",
                    changes=[_change("Course Lesson", existing[0].get("name") or title, "existing")],
                    status="noop", view={"type": "course", "course": course},
                )
            result = frappe.create_document("Course Lesson", {
                "course": course, "chapter": chapter, "title": title, "body": body,
            })
            name, op = result.get("data", {}).get("name") or title, "create"
            ensure_lesson_reference(frappe, chapter, name)
            undo = {"op": "delete", "doctype": "Course Lesson", "name": name}
        if draft_id and member:
            features.mark_lesson_draft_status(member, draft_id, "published")
        return action_result(
            "publish_lesson_draft", "Đã xuất bản bài học", f"Bản nháp đã được ghi vào {course}.",
            changes=[_change("Course Lesson", name, op)], undo=undo,
            view={"type": "course", "course": course},
        )

    reg.register(Tool(
        "publish_lesson_draft",
        "Lưu/xuất bản/tạo/cập nhật bài học vào LMS (Course Lesson). GỌI NGAY khi người dùng nói 'lưu vào lms', 'xuất bản', 'tạo bài', 'ghi vào khóa học', 'lưu bài'. Nếu workflow state có active_draft.id thì truyền draft_id để dùng đúng bản nháp bền vững. Dùng course = LMS Course name/id và chapter = Course Chapter id từ get_course_outline; bắt buộc chapter khi tạo bài mới. Dùng lesson = Course Lesson id khi cập nhật bài đã có.",
        {"type": "object", "properties": {"draft_id": {"type": "string", "description": "ID bản nháp bền vững trong workflow state"}, "course": {"type": "string", "description": "LMS Course name/id"}, "lesson": {"type": "string", "description": "Course Lesson id đã có để cập nhật; để trống khi tạo mới"}, "chapter": {"type": "string", "description": "Course Chapter id, bắt buộc khi tạo mới"}, "title": {"type": "string", "description": "Tiêu đề bài học"}, "body": {"type": "string", "description": "Nội dung markdown đầy đủ của bài học"}}},
        publish_lesson_draft, needs_approval=True, approval_roles={"teacher", "admin"},
    ))

    def analyze_course_gaps(a: dict):
        course = str(a.get("course") or "")
        risky = features.risk_overview(course, 10)
        after = "\n".join(
            f"- Bổ sung hướng dẫn cho {item.get('weak_concepts', [{}])[0].get('label', 'concept yếu')}"
            for item in risky if item.get("weak_concepts")
        ) or "Chưa có gap đủ tin cậy để đề xuất."
        return client_directive("render_view", spec={
            "type": "diff", "doctype": "Course", "name": course or "all",
            "before": "Phân tích gap đang dùng dữ liệu mastery hiện có.", "after": after,
        })

    reg.register(Tool(
        "analyze_course_gaps", "Phân tích concept nhiều học viên sai và trả diff view cho teacher/admin.",
        {"type": "object", "properties": {"course": {"type": "string"}}, "required": []}, analyze_course_gaps,
    ))
