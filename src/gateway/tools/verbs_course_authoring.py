"""Complete, approval-gated course authoring verbs for teacher and admin roles."""
from __future__ import annotations
import json

from typing import Any

from gateway.runtime import write_plans
from gateway.runtime.tool_registry import Tool, ToolRegistry
from gateway.tools.envelope import action_result

_APPROVERS = {"teacher", "admin"}

COURSE_FIELD_SET = {
    "title", "description", "short_introduction", "category", "video_link", "published", "upcoming",
    "featured", "disable_self_learning", "enforce_lesson_completion", "paid_course", "enable_certification",
    "course_price", "currency", "evaluator", "timezone", "card_gradient", "instructors", "related_courses",
}
LESSON_FIELD_SET = {"title", "body", "content", "youtube", "include_in_preview", "instructor_content", "instructor_notes", "quiz_id", "question", "file_type"}
QUIZ_FIELD_SET = {"title", "course", "lesson", "max_attempts", "show_answers", "show_submission_history", "passing_percentage", "duration", "shuffle_questions", "limit_questions_to", "enable_negative_marking", "marks_to_cut"}
ASSIGNMENT_FIELD_SET = {"title", "question", "type", "course", "grade_assignment", "show_answer", "answer"}
EXERCISE_FIELD_SET = {"title", "problem_statement", "language", "test_cases"}


def created_name(response: dict, fallback: str) -> str:
    return _name(response, fallback)


def snapshot_document(frappe, doctype: str, name: str) -> dict:
    return _data(frappe.get_document(doctype, name))


def normalize_course_fields(fields: dict) -> dict:
    normalized = dict(fields or {})
    if "instructors" in normalized:
        normalized["instructors"] = [{"instructor": item} for item in normalized["instructors"]]
    if "related_courses" in normalized:
        normalized["related_courses"] = [{"course": item} for item in normalized["related_courses"]]
    return normalized


def child_names(document: dict, table: str, field: str) -> list[str]:
    return _child_names(document, table, field)


def replace_child_table(frappe, doctype: str, name: str, table: str, field: str, ordered: list[str]) -> None:
    _replace_child_table(frappe, doctype, name, table, field, ordered)


def require_text(value: object, label: str) -> str:
    return _require(value, label)


def create_quiz_questions(frappe, items: list[dict]) -> list[str]:
    created: list[str] = []
    try:
        for item in items or []:
            document = dict((item or {}).get("document") or {})
            result = frappe.create_document("LMS Question", document)
            created.append(_name(result, str(document.get("question") or "")))
    except Exception:
        for question in reversed(created):
            try:
                frappe.delete_document("LMS Question", question)
            except Exception:
                pass
        raise
    return created


_COURSE_PREVIEW_FLAGS = ("dry_run", "preview", "preview_only", "plan_only")


def _course_plan_is_preview(args: dict) -> bool:
    """Preview-only dry-run switch; when truthy a write plan is built with reads only."""
    data = dict(args or {})
    return any(bool(data.get(flag)) for flag in _COURSE_PREVIEW_FLAGS)


def _course_plan_member(args: dict) -> str:
    return str((args or {}).get("member") or "")


def _course_plan_args(args: dict) -> dict:
    return {key: value for key, value in dict(args or {}).items() if not str(key).startswith("_")}


def _course_plan_expected(document: dict) -> str:
    return str((document or {}).get("modified") or "")


def _course_plan_finish(*, action: str, title: str, summary: str, args: dict, operation: str, items: list[dict], changes: list[dict], expected_modified: dict, preview: str, requires_edit_review: bool = False, has_questions: bool = False, view: dict | None = None) -> dict:
    """Persist a write plan (SQLite only) and return the pending_approval envelope. No Frappe writes."""
    reversibility = write_plans.reversibility_for(action, operation, has_questions=has_questions)
    plan = write_plans.create_plan(
        tool=action,
        member=_course_plan_member(args),
        args=_course_plan_args(args),
        items=items,
        changes=changes,
        reversibility=reversibility,
        expected_modified=expected_modified,
    )
    typed_confirm = None
    if write_plans.requires_typed_confirm(action, operation, reversibility):
        typed_confirm = str((args or {}).get("course") or (args or {}).get("chapter") or (args or {}).get("lesson") or (args or {}).get("quiz") or (args or {}).get("name") or operation)
    return action_result(
        action, title, summary, changes,
        status="pending_approval",
        plan_id=plan["id"],
        items=plan["items"],
        requires_edit_review=requires_edit_review,
        preview=preview or summary,
        reversibility=reversibility,
        typed_confirm=typed_confirm,
        view=view,
    )


def _change(doctype: str, name: str, op: str) -> dict:
    return {"doctype": doctype, "name": str(name), "op": op}


def _data(response: dict) -> dict:
    return dict(response.get("data") or {})


def _pick(args: dict, allowed: set[str]) -> dict:
    return {key: args[key] for key in allowed if key in args and args[key] is not None}


def _name(response: dict, fallback: str) -> str:
    return str(_data(response).get("name") or fallback)


def _require(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{label} is required")
    return result


def _child_names(document: dict, table: str, field: str) -> list[str]:
    return [str(row.get(field)) for row in document.get(table, []) if row.get(field)]


def _replace_child_table(frappe, doctype: str, name: str, table: str, field: str, ordered: list[str]) -> None:
    frappe.update_document(doctype, name, {table: [{field: item} for item in ordered]})


def ensure_lesson_reference(frappe, chapter: str, lesson: str) -> bool:
    """Ensure a created lesson is visible in its chapter; return whether a row was added."""
    document = _data(frappe.get_document("Course Chapter", chapter))
    lessons = _child_names(document, "lessons", "lesson")
    if lesson in lessons:
        return False
    _replace_child_table(frappe, "Course Chapter", chapter, "lessons", "lesson", [*lessons, lesson])
    return True


def _register_mutation(reg: ToolRegistry, name: str, description: str, parameters: dict, func) -> None:
    reg.register(Tool(name, description, parameters, func, needs_approval=True, approval_roles=_APPROVERS))


def register(reg: ToolRegistry, frappe) -> None:
    def get_course_authoring_state(a: dict):
        course = _require(a.get("course"), "course")
        course_doc = _data(frappe.get_document("LMS Course", course))
        chapters = []
        for chapter_id in _child_names(course_doc, "chapters", "chapter"):
            chapter = _data(frappe.get_document("Course Chapter", chapter_id))
            lessons = []
            for lesson_id in _child_names(chapter, "lessons", "lesson"):
                lessons.append(_data(frappe.get_document("Course Lesson", lesson_id)))
            chapters.append({**chapter, "lesson_documents": lessons})
        quizzes = frappe.list_documents(
            "LMS Quiz", filters={"course": course},
            fields=["name", "title", "lesson", "passing_percentage", "total_marks"], limit=500,
        ).get("data", [])
        assignments = frappe.list_documents(
            "LMS Assignment", filters={"course": course},
            fields=["name", "title", "type", "grade_assignment"], limit=500,
        ).get("data", [])
        return {"course": course_doc, "chapters": chapters, "quizzes": quizzes, "assignments": assignments}

    reg.register(Tool(
        "get_course_authoring_state",
        "Đọc toàn bộ cấu trúc tác giả của một khóa học: metadata, chương, bài, quiz và assignment. Dùng trước khi sửa hoặc sắp xếp.",
        {"type": "object", "properties": {"course": {"type": "string"}}, "required": ["course"]},
        get_course_authoring_state,
    ))

    course_fields = {
        "title", "description", "short_introduction", "category", "video_link", "published", "upcoming",
        "featured", "disable_self_learning", "enforce_lesson_completion", "paid_course", "enable_certification",
        "course_price", "currency", "evaluator", "timezone", "card_gradient", "instructors", "related_courses",
    }

    def manage_course(a: dict):
        operation = _require(a.get("operation"), "operation")
        if operation == "create":
            for field in ("title", "description", "short_introduction"):
                _require(a.get(field), field)
            fields = _pick(a, course_fields)
            if "instructors" in fields:
                fields["instructors"] = [{"instructor": item} for item in fields["instructors"]]
            if "related_courses" in fields:
                fields["related_courses"] = [{"course": item} for item in fields["related_courses"]]
            if _course_plan_is_preview(a):
                return _course_plan_finish(
                    action="manage_course", title="Xem trước tạo khóa học",
                    summary=f"Xem trước tạo khóa học {fields.get('title', '')}.",
                    args=a, operation=operation,
                    items=[{
                        "label": f"Tạo khóa học {fields.get('title', '')}", "kind": "create",
                        "preview": {"before": None, "after": fields},
                        "payload": {"doctype": "LMS Course", "fields": fields},
                    }],
                    changes=[_change("LMS Course", str(fields.get("title", "") or "(mới)"), "create")],
                    expected_modified={},
                    preview=f"Tạo LMS Course {fields.get('title', '')}.",
                    view={"type": "course", "course": str(fields.get("title", "") or "")},
                )
            result = frappe.create_document("LMS Course", fields)
            name = _name(result, fields["title"])
            return action_result("manage_course", "Đã tạo khóa học", f"Đã tạo khóa học {fields['title']}.", [_change("LMS Course", name, "create")], undo={"op": "delete", "doctype": "LMS Course", "name": name}, view={"type": "course", "course": name})
        course = _require(a.get("course"), "course")
        if operation == "delete":
            if _course_plan_is_preview(a):
                before = _data(frappe.get_document("LMS Course", course))
                key = write_plans.plan_key("LMS Course", course)
                return _course_plan_finish(
                    action="manage_course", title="Xem trước xóa khóa học",
                    summary=f"Xem trước xóa khóa học {course} cùng cấu trúc phụ thuộc.",
                    args=a, operation=operation,
                    items=[{"label": f"Xóa khóa học {course}", "kind": "delete", "preview": {"before": before}, "payload": {"doctype": "LMS Course", "name": course, "method": "lms.lms.api.delete_course"}}],
                    changes=[_change("LMS Course", course, "delete")],
                    expected_modified={key: _course_plan_expected(before)},
                    preview=f"Xóa LMS Course {course} (cascade).",
                    view={"type": "course", "course": course},
                )
            frappe.call_method("lms.lms.api.delete_course", course=course)
            return action_result("manage_course", "Đã xóa khóa học", f"Đã xóa khóa học {course} cùng cấu trúc phụ thuộc.", [_change("LMS Course", course, "delete")])
        if operation != "update":
            raise ValueError("operation must be create, update or delete")
        fields = _pick(a, course_fields)
        if not fields:
            raise ValueError("at least one course field is required for update")
        if _course_plan_is_preview(a):
            before = _data(frappe.get_document("LMS Course", course))
            normalized = dict(fields)
            if "instructors" in normalized:
                normalized["instructors"] = [{"instructor": item} for item in normalized["instructors"]]
            if "related_courses" in normalized:
                normalized["related_courses"] = [{"course": item} for item in normalized["related_courses"]]
            key = write_plans.plan_key("LMS Course", course)
            items = [{"label": f"Cập nhật {name}: {before.get(name)!r} → {value!r}", "kind": "field", "preview": {"before": before.get(name), "after": value}, "payload": {"doctype": "LMS Course", "name": course, "fields": {name: value}}} for name, value in normalized.items()]
            return _course_plan_finish(
                action="manage_course", title="Xem trước cập nhật khóa học",
                summary=f"Xem trước cập nhật {len(normalized)} trường của {course}.",
                args=a, operation=operation, items=items,
                changes=[_change("LMS Course", course, "update")],
                expected_modified={key: _course_plan_expected(before)},
                preview=f"Cập nhật {len(normalized)} trường của LMS Course {course}.",
                view={"type": "course", "course": course},
            )
        before = _data(frappe.get_document("LMS Course", course))
        if "instructors" in fields:
            fields["instructors"] = [{"instructor": item} for item in fields["instructors"]]
        if "related_courses" in fields:
            fields["related_courses"] = [{"course": item} for item in fields["related_courses"]]
        frappe.update_document("LMS Course", course, fields)
        undo_fields = {key: before.get(key) for key in fields}
        return action_result("manage_course", "Đã cập nhật khóa học", f"Đã cập nhật {len(fields)} trường của {course}.", [_change("LMS Course", course, "update")], undo={"op": "restore", "doctype": "LMS Course", "name": course, "fields": undo_fields}, view={"type": "course", "course": course})

    _register_mutation(reg, "manage_course", "Tạo, cập nhật, xuất bản hoặc xóa khóa học LMS. Xóa là cascade và luôn cần phê duyệt rõ ràng.", {
        "type": "object", "properties": {
            "operation": {"type": "string", "enum": ["create", "update", "reorder"]}, "course": {"type": "string"},
            "title": {"type": "string"}, "description": {"type": "string"}, "short_introduction": {"type": "string"},
            "category": {"type": "string"}, "video_link": {"type": "string"}, "published": {"type": "integer", "enum": [0, 1]},
            "upcoming": {"type": "integer", "enum": [0, 1]}, "featured": {"type": "integer", "enum": [0, 1]},
            "disable_self_learning": {"type": "integer", "enum": [0, 1]}, "enforce_lesson_completion": {"type": "integer", "enum": [0, 1]},
            "paid_course": {"type": "integer", "enum": [0, 1]}, "enable_certification": {"type": "integer", "enum": [0, 1]},
            "course_price": {"type": "number"}, "currency": {"type": "string"}, "evaluator": {"type": "string"},
            "timezone": {"type": "string"}, "card_gradient": {"type": "string"},
            "instructors": {"type": "array", "items": {"type": "string"}}, "related_courses": {"type": "array", "items": {"type": "string"}},
            "dry_run": {"type": "boolean", "description": "Preview-only plan; performs reads and returns pending_approval without Frappe writes"},
        }, "required": ["operation"],
    }, manage_course)

    def manage_chapter(a: dict):
        operation = _require(a.get("operation"), "operation")
        if operation == "create":
            course, title = _require(a.get("course"), "course"), _require(a.get("title"), "title")
            if _course_plan_is_preview(a):
                course_doc = _data(frappe.get_document("LMS Course", course))
                key = write_plans.plan_key("LMS Course", course)
                return _course_plan_finish(
                    action="manage_chapter", title="Xem trước tạo chương",
                    summary=f"Xem trước tạo chương {title} trong {course}.",
                    args=a, operation=operation,
                    items=[
                        {"label": f"Tạo chương {title}", "kind": "create", "preview": {"before": None, "after": {"course": course, "title": title}}, "payload": {"doctype": "Course Chapter", "fields": {"course": course, "title": title}}},
                        {"label": f"Gắn chương vào {course}", "kind": "update", "preview": {"before": course_doc.get("chapters"), "after": "append chapter"}, "payload": {"doctype": "LMS Course", "name": course, "action": "append-chapter"}},
                    ],
                    changes=[_change("Course Chapter", "(mới)", "create"), _change("LMS Course", course, "update")],
                    expected_modified={key: _course_plan_expected(course_doc)},
                    preview=f"Tạo Course Chapter {title} trong {course}.",
                    view={"type": "course", "course": course},
                )
            created = frappe.create_document("Course Chapter", {"course": course, "title": title})
            chapter = _name(created, title)
            document = _data(frappe.get_document("LMS Course", course))
            chapters = _child_names(document, "chapters", "chapter")
            if chapter not in chapters:
                _replace_child_table(frappe, "LMS Course", course, "chapters", "chapter", [*chapters, chapter])
            return action_result("manage_chapter", "Đã tạo chương", f"Đã thêm {title} vào {course}.", [_change("Course Chapter", chapter, "create"), _change("LMS Course", course, "update")], undo={"op": "delete", "doctype": "Course Chapter", "name": chapter}, view={"type": "course", "course": course})
        if operation == "reorder":
            course = _require(a.get("course"), "course")
            ordered = [str(item) for item in a.get("ordered_chapters") or []]
            course_doc = _data(frappe.get_document("LMS Course", course))
            current = _child_names(course_doc, "chapters", "chapter")
            if len(ordered) != len(set(ordered)) or set(ordered) != set(current):
                raise ValueError("ordered_chapters must contain every current chapter exactly once")
            if _course_plan_is_preview(a):
                key = write_plans.plan_key("LMS Course", course)
                return _course_plan_finish(
                    action="manage_chapter", title="Xem trước sắp xếp chương",
                    summary=f"Xem trước sắp xếp {len(ordered)} chương trong {course}.",
                    args=a, operation=operation,
                    items=[{"label": f"Sắp xếp {len(ordered)} chương", "kind": "reorder", "preview": {"before": current, "after": ordered}, "payload": {"doctype": "LMS Course", "name": course, "fields": {"ordered_chapters": ordered}}}],
                    changes=[_change("LMS Course", course, "reorder")],
                    expected_modified={key: _course_plan_expected(course_doc)},
                    preview=f"Sắp xếp {len(ordered)} chương trong {course}.",
                    view={"type": "course", "course": course},
                )
            _replace_child_table(frappe, "LMS Course", course, "chapters", "chapter", ordered)
            return action_result("manage_chapter", "Đã sắp xếp chương", f"Đã sắp xếp {len(ordered)} chương trong {course}.", [_change("LMS Course", course, "reorder")], undo={"op": "restore", "doctype": "LMS Course", "name": course, "fields": {"chapters": [{"chapter": item} for item in current]}}, view={"type": "course", "course": course})
        chapter = _require(a.get("chapter"), "chapter")
        if operation == "delete":
            if _course_plan_is_preview(a):
                before = _data(frappe.get_document("Course Chapter", chapter))
                key = write_plans.plan_key("Course Chapter", chapter)
                return _course_plan_finish(
                    action="manage_chapter", title="Xem trước xóa chương",
                    summary=f"Xem trước xóa chương {chapter} và các bài trong chương.",
                    args=a, operation=operation,
                    items=[{"label": f"Xóa chương {chapter}", "kind": "delete", "preview": {"before": before}, "payload": {"doctype": "Course Chapter", "name": chapter, "method": "lms.lms.api.delete_chapter"}}],
                    changes=[_change("Course Chapter", chapter, "delete")],
                    expected_modified={key: _course_plan_expected(before)},
                    preview=f"Xóa Course Chapter {chapter} (cascade).",
                    view={"type": "course", "course": before.get("course")},
                )
            frappe.call_method("lms.lms.api.delete_chapter", chapter=chapter)
            return action_result("manage_chapter", "Đã xóa chương", f"Đã xóa chương {chapter} và các bài trong chương.", [_change("Course Chapter", chapter, "delete")])
        if operation != "update":
            raise ValueError("operation must be create, update, reorder or delete")
        title = _require(a.get("title"), "title")
        if _course_plan_is_preview(a):
            before = _data(frappe.get_document("Course Chapter", chapter))
            key = write_plans.plan_key("Course Chapter", chapter)
            return _course_plan_finish(
                action="manage_chapter", title="Xem trước cập nhật chương",
                summary=f"Xem trước đổi tiêu đề chương {chapter}.",
                args=a, operation=operation,
                items=[{"label": f"Đổi tiêu đề: {before.get('title', '')!r} → {title!r}", "kind": "field", "preview": {"before": before.get("title", ""), "after": title}, "payload": {"doctype": "Course Chapter", "name": chapter, "fields": {"title": title}}}],
                changes=[_change("Course Chapter", chapter, "update")],
                expected_modified={key: _course_plan_expected(before)},
                preview=f"Đổi tiêu đề Course Chapter {chapter}.",
            )
        before = _data(frappe.get_document("Course Chapter", chapter))
        frappe.update_document("Course Chapter", chapter, {"title": title})
        return action_result("manage_chapter", "Đã cập nhật chương", f"Đã đổi tiêu đề chương {chapter}.", [_change("Course Chapter", chapter, "update")], undo={"op": "restore", "doctype": "Course Chapter", "name": chapter, "fields": {"title": before.get("title", "")}})

    _register_mutation(reg, "manage_chapter", "Tạo, đổi tên, sắp xếp hoặc xóa chương; tự đồng bộ bảng chương của khóa học.", {
        "type": "object", "properties": {"operation": {"type": "string", "enum": ["create", "update", "reorder"]}, "course": {"type": "string"}, "chapter": {"type": "string"}, "title": {"type": "string"}, "ordered_chapters": {"type": "array", "items": {"type": "string"}}, "dry_run": {"type": "boolean"}}, "required": ["operation"],
    }, manage_chapter)

    lesson_fields = {"title", "body", "content", "youtube", "include_in_preview", "instructor_content", "instructor_notes", "quiz_id", "question", "file_type"}

    def manage_lesson(a: dict):
        operation = _require(a.get("operation"), "operation")
        if operation == "create":
            course = _require(a.get("course"), "course")
            chapter = _require(a.get("chapter"), "chapter")
            title = _require(a.get("title"), "title")
            fields = {"course": course, "chapter": chapter, **_pick(a, lesson_fields)}
            if _course_plan_is_preview(a):
                chapter_doc = _data(frappe.get_document("Course Chapter", chapter))
                key = write_plans.plan_key("Course Chapter", chapter)
                return _course_plan_finish(
                    action="manage_lesson", title="Xem trước tạo bài học",
                    summary=f"Xem trước tạo bài {title} trong {chapter}.",
                    args=a, operation=operation,
                    items=[
                        {"label": f"Tạo bài {title}", "kind": "create", "preview": {"before": None, "after": fields}, "payload": {"doctype": "Course Lesson", "fields": fields}},
                        {"label": f"Gắn bài vào {chapter}", "kind": "update", "preview": {"before": chapter_doc.get("lessons"), "after": "append lesson"}, "payload": {"doctype": "Course Chapter", "name": chapter, "action": "append-lesson"}},
                    ],
                    changes=[_change("Course Lesson", "(mới)", "create"), _change("Course Chapter", chapter, "update")],
                    expected_modified={key: _course_plan_expected(chapter_doc)},
                    preview=f"Tạo Course Lesson {title} trong {chapter}.",
                    view={"type": "course", "course": course},
                )
            created = frappe.create_document("Course Lesson", fields)
            lesson = _name(created, title)
            ensure_lesson_reference(frappe, chapter, lesson)
            return action_result("manage_lesson", "Đã tạo bài học", f"Đã thêm {title} vào {chapter}.", [_change("Course Lesson", lesson, "create"), _change("Course Chapter", chapter, "update")], undo={"op": "delete", "doctype": "Course Lesson", "name": lesson}, view={"type": "course", "course": course})
        if operation == "reorder":
            chapter = _require(a.get("chapter"), "chapter")
            ordered = [str(item) for item in a.get("ordered_lessons") or []]
            chapter_doc = _data(frappe.get_document("Course Chapter", chapter))
            current = _child_names(chapter_doc, "lessons", "lesson")
            if len(ordered) != len(set(ordered)) or set(ordered) != set(current):
                raise ValueError("ordered_lessons must contain every current lesson exactly once")
            if _course_plan_is_preview(a):
                key = write_plans.plan_key("Course Chapter", chapter)
                return _course_plan_finish(
                    action="manage_lesson", title="Xem trước sắp xếp bài học",
                    summary=f"Xem trước sắp xếp {len(ordered)} bài trong {chapter}.",
                    args=a, operation=operation,
                    items=[{"label": f"Sắp xếp {len(ordered)} bài", "kind": "reorder", "preview": {"before": current, "after": ordered}, "payload": {"doctype": "Course Chapter", "name": chapter, "fields": {"ordered_lessons": ordered}}}],
                    changes=[_change("Course Chapter", chapter, "reorder")],
                    expected_modified={key: _course_plan_expected(chapter_doc)},
                    preview=f"Sắp xếp {len(ordered)} bài trong {chapter}.",
                )
            _replace_child_table(frappe, "Course Chapter", chapter, "lessons", "lesson", ordered)
            return action_result("manage_lesson", "Đã sắp xếp bài học", f"Đã sắp xếp {len(ordered)} bài trong {chapter}.", [_change("Course Chapter", chapter, "reorder")], undo={"op": "restore", "doctype": "Course Chapter", "name": chapter, "fields": {"lessons": [{"lesson": item} for item in current]}})
        lesson = _require(a.get("lesson"), "lesson")
        if operation == "delete":
            chapter = _require(a.get("chapter"), "chapter")
            if _course_plan_is_preview(a):
                before = _data(frappe.get_document("Course Lesson", lesson))
                host = _data(frappe.get_document("Course Chapter", chapter))
                key = write_plans.plan_key("Course Lesson", lesson)
                host_key = write_plans.plan_key("Course Chapter", chapter)
                return _course_plan_finish(
                    action="manage_lesson", title="Xem trước xóa bài học",
                    summary=f"Xem trước xóa bài {lesson} khỏi {chapter}.",
                    args=a, operation=operation,
                    items=[{"label": f"Xóa bài {lesson} khỏi {chapter}", "kind": "delete", "preview": {"before": before}, "payload": {"doctype": "Course Lesson", "name": lesson, "chapter": chapter, "method": "lms.lms.api.delete_lesson"}}],
                    changes=[_change("Course Lesson", lesson, "delete")],
                    expected_modified={key: _course_plan_expected(before), host_key: _course_plan_expected(host)},
                    preview=f"Xóa Course Lesson {lesson} khỏi {chapter}.",
                    view={"type": "course", "course": before.get("course")},
                )
            frappe.call_method("lms.lms.api.delete_lesson", lesson=lesson, chapter=chapter)
            return action_result("manage_lesson", "Đã xóa bài học", f"Đã xóa bài {lesson} khỏi {chapter}.", [_change("Course Lesson", lesson, "delete")])
        if operation != "update":
            raise ValueError("operation must be create, update, reorder or delete")
        fields = _pick(a, lesson_fields)
        if not fields:
            raise ValueError("at least one lesson field is required for update")
        if _course_plan_is_preview(a):
            before = _data(frappe.get_document("Course Lesson", lesson))
            key = write_plans.plan_key("Course Lesson", lesson)
            items = [{"label": f"Cập nhật {name}: {before.get(name)!r} → {value!r}", "kind": "field", "preview": {"before": before.get(name), "after": value}, "payload": {"doctype": "Course Lesson", "name": lesson, "fields": {name: value}}} for name, value in fields.items()]
            return _course_plan_finish(
                action="manage_lesson", title="Xem trước cập nhật bài học",
                summary=f"Xem trước cập nhật {len(fields)} trường của {lesson}.",
                args=a, operation=operation, items=items,
                changes=[_change("Course Lesson", lesson, "update")],
                expected_modified={key: _course_plan_expected(before)},
                preview=f"Cập nhật {len(fields)} trường của Course Lesson {lesson}.",
                view={"type": "course", "course": before.get("course")},
            )
        before = _data(frappe.get_document("Course Lesson", lesson))
        frappe.update_document("Course Lesson", lesson, fields)
        return action_result("manage_lesson", "Đã cập nhật bài học", f"Đã cập nhật {len(fields)} trường của {lesson}.", [_change("Course Lesson", lesson, "update")], undo={"op": "restore", "doctype": "Course Lesson", "name": lesson, "fields": {key: before.get(key) for key in fields}}, view={"type": "course", "course": before.get("course")})

    _register_mutation(reg, "manage_lesson", "Tạo, sửa nội dung/EditorJS, sắp xếp hoặc xóa bài học; tự đồng bộ bảng bài của chương.", {
        "type": "object", "properties": {"operation": {"type": "string", "enum": ["create", "update", "reorder"]}, "course": {"type": "string"}, "chapter": {"type": "string"}, "lesson": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"}, "content": {"type": "string", "description": "EditorJS JSON"}, "youtube": {"type": "string"}, "include_in_preview": {"type": "integer", "enum": [0, 1]}, "instructor_content": {"type": "string"}, "instructor_notes": {"type": "string"}, "quiz_id": {"type": "string"}, "question": {"type": "string"}, "file_type": {"type": "string"}, "ordered_lessons": {"type": "array", "items": {"type": "string"}}, "dry_run": {"type": "boolean"}}, "required": ["operation"],
    }, manage_lesson)

    def manage_lesson_block(a: dict):
        operation = _require(a.get("operation"), "operation")
        lesson = _require(a.get("lesson"), "lesson")
        block_type = _require(a.get("block_type"), "block_type")
        resource = _require(a.get("resource"), "resource")
        data_key = {"quiz": "quiz", "assignment": "assignment", "program": "exercise"}.get(block_type)
        if data_key is None:
            raise ValueError("block_type must be quiz, assignment or program")
        document = _data(frappe.get_document("Course Lesson", lesson))
        old_content = str(document.get("content") or "")
        if old_content:
            try:
                content = json.loads(old_content)
            except (TypeError, ValueError) as exc:
                raise ValueError("lesson content is not valid EditorJS JSON") from exc
            if not isinstance(content, dict) or not isinstance(content.get("blocks"), list):
                raise ValueError("lesson content must be an EditorJS object with a blocks array")
        else:
            blocks = []
            if document.get("body"):
                blocks.append({"type": "markdown", "data": {"text": document["body"]}})
            content = {"blocks": blocks}
        blocks = content["blocks"]
        matches = [
            index for index, block in enumerate(blocks)
            if isinstance(block, dict)
            and block.get("type") == block_type
            and isinstance(block.get("data"), dict)
            and block["data"].get(data_key) == resource
        ]
        if operation == "attach":
            if matches:
                return action_result("manage_lesson_block", "Nội dung đã được gắn", f"{resource} đã có trong {lesson}.", [_change("Course Lesson", lesson, "existing")], status="noop")
            blocks.append({"type": block_type, "data": {data_key: resource}})
        elif operation == "detach":
            if not matches:
                return action_result("manage_lesson_block", "Không có nội dung để gỡ", f"{resource} không có trong {lesson}.", [_change("Course Lesson", lesson, "missing")], status="noop")
            content["blocks"] = [block for index, block in enumerate(blocks) if index not in matches]
        else:
            raise ValueError("operation must be attach or detach")
        new_content = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        if _course_plan_is_preview(a):
            key = write_plans.plan_key("Course Lesson", lesson)
            return _course_plan_finish(
                action="manage_lesson_block", title="Xem trước cập nhật nội dung bài học",
                summary=f"Xem trước {operation} {block_type} {resource} trong {lesson}.",
                args=a, operation=operation,
                items=[{"label": f"{operation} {block_type} {resource}", "kind": "block", "preview": {"before": old_content, "after": new_content}, "payload": {"doctype": "Course Lesson", "name": lesson, "fields": {"content": new_content}}}],
                changes=[_change("Course Lesson", lesson, operation)],
                expected_modified={key: _course_plan_expected(document)},
                preview=f"{operation} {block_type} {resource} trong Course Lesson {lesson}.",
                view={"type": "course", "course": document.get("course")},
            )
        frappe.update_document("Course Lesson", lesson, {"content": new_content})
        return action_result(
            "manage_lesson_block",
            "Đã cập nhật nội dung bài học",
            f"Đã {operation} {block_type} {resource} trong {lesson}.",
            [_change("Course Lesson", lesson, operation)],
            undo={"op": "restore", "doctype": "Course Lesson", "name": lesson, "fields": {"content": old_content}},
            view={"type": "course", "course": document.get("course")},
        )

    _register_mutation(reg, "manage_lesson_block", "Gắn hoặc gỡ quiz, assignment hay bài lập trình khỏi luồng nội dung EditorJS của một bài học; giữ nguyên nội dung hiện có.", {
        "type": "object", "properties": {
            "operation": {"type": "string", "enum": ["attach", "detach"]},
            "lesson": {"type": "string"},
            "block_type": {"type": "string", "enum": ["quiz", "assignment", "program"]},
            "resource": {"type": "string", "description": "Tên/id của LMS Quiz, LMS Assignment hoặc LMS Programming Exercise"},
            "dry_run": {"type": "boolean"},
        }, "required": ["operation", "lesson", "block_type", "resource"],
    }, manage_lesson_block)

    question_schema = {"type": "object", "properties": {"question": {"type": "string"}, "type": {"type": "string", "enum": ["Choices", "User Input", "Open Ended"]}, "multiple": {"type": "integer", "enum": [0, 1]}, "marks": {"type": "integer", "minimum": 1}, "options": {"type": "array", "maxItems": 10, "items": {"type": "object", "properties": {"text": {"type": "string"}, "correct": {"type": "integer", "enum": [0, 1]}, "explanation": {"type": "string"}}, "required": ["text"]}}}, "required": ["question", "type", "marks"]}
    quiz_fields = {"title", "course", "lesson", "max_attempts", "show_answers", "show_submission_history", "passing_percentage", "duration", "shuffle_questions", "limit_questions_to", "enable_negative_marking", "marks_to_cut"}
    def _preview_quiz_document(item: dict):
        """Validate one quiz question and build its LMS Question document without any Frappe write."""
        document = _pick(item, {"question", "type", "multiple"})
        _require(document.get("question"), "question.question")
        _require(document.get("type"), "question.type")
        for index, option in enumerate(item.get("options") or [], start=1):
            document[f"option_{index}"] = option.get("text", "")
            document[f"is_correct_{index}"] = int(option.get("correct") or 0)
            document[f"explanation_{index}"] = option.get("explanation", "")
        return document, max(1, int(item.get("marks") or 1))

    def _preview_field_items(doctype, target, before, fields):
        """Expand scalar fields (and exercise test cases) into per-item previews with before/after data."""
        items = []
        for name in sorted(fields):
            value = fields[name]
            scope = {"doctype": doctype, "fields": {name: value}}
            if target:
                scope["name"] = target
            if isinstance(value, list):
                old_entries = ((before or {}).get(name) or []) if target else []
                for index, entry in enumerate(value):
                    old_entry = old_entries[index] if index < len(old_entries) else None
                    kind = "test_case" if doctype == "LMS Programming Exercise" else "field"
                    items.append({"label": f"{name}[{index + 1}]", "kind": kind, "preview": {"before": old_entry, "after": entry}, "payload": {**scope, "field": name, "index": index, "entry": entry}})
            else:
                if target:
                    items.append({"label": f"Cập nhật {name}: {(before or {}).get(name)!r} → {value!r}", "kind": "field", "preview": {"before": (before or {}).get(name), "after": value}, "payload": scope})
                else:
                    items.append({"label": f"Tạo {name}: {value!r}", "kind": "field", "preview": {"before": None, "after": value}, "payload": scope})
        return items


    def _quiz_questions(items: list[dict]) -> tuple[list[dict], list[str]]:
        rows, created = [], []
        try:
            for item in items:
                document = _pick(item, {"question", "type", "multiple"})
                _require(document.get("question"), "question.question")
                _require(document.get("type"), "question.type")
                for index, option in enumerate(item.get("options") or [], start=1):
                    document[f"option_{index}"] = option.get("text", "")
                    document[f"is_correct_{index}"] = int(option.get("correct") or 0)
                    document[f"explanation_{index}"] = option.get("explanation", "")
                result = frappe.create_document("LMS Question", document)
                question = _name(result, document["question"])
                created.append(question)
                rows.append({"question": question, "marks": max(1, int(item.get("marks") or 1))})
        except Exception:
            for question in reversed(created):
                try:
                    frappe.delete_document("LMS Question", question)
                except Exception:
                    pass
            raise
        return rows, created

    def manage_quiz(a: dict):
        operation = _require(a.get("operation"), "operation")
        if operation == "delete":
            quiz = _require(a.get("quiz"), "quiz")
            if _course_plan_is_preview(a):
                before = _data(frappe.get_document("LMS Quiz", quiz))
                key = write_plans.plan_key("LMS Quiz", quiz)
                return _course_plan_finish(
                    action="manage_quiz", title="Xem trước xóa quiz",
                    summary=f"Xem trước xóa quiz {quiz}.",
                    args=a, operation=operation,
                    items=[{"label": f"Xóa quiz {quiz}", "kind": "delete", "preview": {"before": before}, "payload": {"doctype": "LMS Quiz", "name": quiz}}],
                    changes=[_change("LMS Quiz", quiz, "delete")],
                    expected_modified={key: _course_plan_expected(before)},
                    preview=f"Xóa LMS Quiz {quiz}.",
                )
            frappe.delete_document("LMS Quiz", quiz)
            return action_result("manage_quiz", "Đã xóa quiz", f"Đã xóa quiz {quiz}.", [_change("LMS Quiz", quiz, "delete")])
        fields = _pick(a, quiz_fields)
        raw_questions = list(a.get("questions") or []) if "questions" in a else None
        if _course_plan_is_preview(a):
            if operation == "create":
                _require(fields.get("title"), "title")
                if not fields and not raw_questions:
                    raise ValueError("at least one quiz field or questions is required for create")
                items = _preview_field_items("LMS Quiz", "", {}, fields)
                planned: list[dict] = []
                for item in raw_questions or []:
                    document, marks = _preview_quiz_document(item)
                    planned.append({"question": f"pending-{len(planned) + 1}", "marks": marks})
                    items.append({"label": f"Câu hỏi: {document.get('question')}", "kind": "question", "preview": {"before": None, "after": {"document": document, "marks": marks}}, "payload": {"doctype": "LMS Question", "document": document, "marks": marks}})
                return _course_plan_finish(
                    action="manage_quiz", title="Xem trước tạo quiz",
                    summary=f"Xem trước tạo quiz {fields.get('title', '')} với {len(raw_questions or [])} câu hỏi.",
                    args=a, operation=operation, items=items,
                    changes=[_change("LMS Quiz", str(fields.get("title") or "pending"), "create")],
                    expected_modified={},
                    has_questions=bool(raw_questions),
                    requires_edit_review=bool(raw_questions),
                    preview=f"Tạo LMS Quiz {fields.get('title', '')} với {len(raw_questions or [])} câu hỏi.",
                )
            if operation != "update":
                raise ValueError("operation must be create, update or delete")
            quiz = _require(a.get("quiz"), "quiz")
            if not fields and raw_questions is None:
                raise ValueError("at least one quiz field or questions is required for update")
            before = _data(frappe.get_document("LMS Quiz", quiz))
            items = _preview_field_items("LMS Quiz", quiz, before, fields)
            for item in raw_questions or []:
                document, marks = _preview_quiz_document(item)
                items.append({"label": f"Câu hỏi: {document.get('question')}", "kind": "question", "preview": {"before": None, "after": {"document": document, "marks": marks}}, "payload": {"doctype": "LMS Question", "quiz": quiz, "document": document, "marks": marks}})
            key = write_plans.plan_key("LMS Quiz", quiz)
            return _course_plan_finish(
                action="manage_quiz", title="Xem trước cập nhật quiz",
                summary=f"Xem trước cập nhật quiz {quiz}.",
                args=a, operation=operation, items=items,
                changes=[_change("LMS Quiz", quiz, "update")],
                expected_modified={key: _course_plan_expected(before)},
                has_questions=bool(raw_questions),
                requires_edit_review=bool(raw_questions),
                preview=f"Cập nhật LMS Quiz {quiz}.",
            )
        created_questions: list[str] = []
        if "questions" in a:
            fields["questions"], created_questions = _quiz_questions(a.get("questions") or [])
        if operation == "create":
            _require(fields.get("title"), "title")
            try:
                result = frappe.create_document("LMS Quiz", fields)
            except Exception:
                for question in reversed(created_questions):
                    try:
                        frappe.delete_document("LMS Question", question)
                    except Exception:
                        pass
                raise
            quiz = _name(result, fields["title"])
            changes = [_change("LMS Quiz", quiz, "create"), *[_change("LMS Question", item, "create") for item in created_questions]]
            return action_result(
                "manage_quiz", "Đã tạo quiz", f"Đã tạo quiz {fields['title']} với {len(created_questions)} câu hỏi.", changes,
                undo={
                    "op": "restore_many",
                    "restores": [
                        {"doctype": "LMS Quiz", "name": quiz, "delete": True},
                        *[{"doctype": "LMS Question", "name": item, "delete": True} for item in created_questions],
                    ],
                },
            )
        if operation != "update":
            raise ValueError("operation must be create, update or delete")
        quiz = _require(a.get("quiz"), "quiz")
        if not fields:
            raise ValueError("at least one quiz field or questions is required for update")
        before = _data(frappe.get_document("LMS Quiz", quiz))
        frappe.update_document("LMS Quiz", quiz, fields)
        changes = [_change("LMS Quiz", quiz, "update"), *[_change("LMS Question", item, "create") for item in created_questions]]
        undo = {
            "op": "restore_many",
            "restores": [
                {"doctype": "LMS Quiz", "name": quiz, "fields": {key: before.get(key) for key in fields}},
                *[{"doctype": "LMS Question", "name": item, "delete": True} for item in created_questions],
            ],
        }
        return action_result("manage_quiz", "Đã cập nhật quiz", f"Đã cập nhật quiz {quiz}.", changes, undo=undo)

    _register_mutation(reg, "manage_quiz", "Tạo, sửa hoặc xóa quiz và tối đa 10 lựa chọn cho từng câu hỏi. Có thể gắn quiz với course/lesson.", {
        "type": "object", "properties": {"operation": {"type": "string", "enum": ["create", "update"]}, "quiz": {"type": "string"}, "title": {"type": "string"}, "course": {"type": "string"}, "lesson": {"type": "string"}, "max_attempts": {"type": "integer"}, "show_answers": {"type": "integer", "enum": [0, 1]}, "show_submission_history": {"type": "integer", "enum": [0, 1]}, "passing_percentage": {"type": "integer", "minimum": 0, "maximum": 100}, "duration": {"type": "integer"}, "shuffle_questions": {"type": "integer", "enum": [0, 1]}, "limit_questions_to": {"type": "integer"}, "enable_negative_marking": {"type": "integer", "enum": [0, 1]}, "marks_to_cut": {"type": "number"}, "questions": {"type": "array", "items": question_schema}, "dry_run": {"type": "boolean", "description": "Preview-only plan; performs reads and returns pending_approval without Frappe writes"}}, "required": ["operation"],
    }, manage_quiz)

    def _simple_manager(a: dict, *, doctype: str, action: str, allowed: set[str], required: tuple[str, ...]):
        operation = _require(a.get("operation"), "operation")
        target = str(a.get("name") or "").strip()
        if operation == "delete":
            _require(target, "name")
            if _course_plan_is_preview(a):
                before = _data(frappe.get_document(doctype, target))
                key = write_plans.plan_key(doctype, target)
                return _course_plan_finish(
                    action=action, title=f"Xem trước xóa {doctype}",
                    summary=f"Xem trước xóa {target}.",
                    args=a, operation=operation,
                    items=[{"label": f"Xóa {target}", "kind": "delete", "preview": {"before": before}, "payload": {"doctype": doctype, "name": target}}],
                    changes=[_change(doctype, target, "delete")],
                    expected_modified={key: _course_plan_expected(before)},
                    preview=f"Xóa {doctype} {target}.",
                )
            frappe.delete_document(doctype, target)
            return action_result(action, f"Đã xóa {doctype}", f"Đã xóa {target}.", [_change(doctype, target, "delete")])
        fields = _pick(a, allowed)
        if _course_plan_is_preview(a):
            if operation == "create":
                for field in required:
                    _require(fields.get(field), field)
                items = _preview_field_items(doctype, "", {}, fields)
                return _course_plan_finish(
                    action=action, title=f"Xem trước tạo {doctype}",
                    summary=f"Xem trước tạo {fields[required[0]]}.",
                    args=a, operation=operation, items=items,
                    changes=[_change(doctype, str(fields[required[0]]), "create")],
                    expected_modified={},
                    requires_edit_review=(doctype == "LMS Programming Exercise" and "test_cases" in fields),
                    preview=f"Tạo {doctype} {fields[required[0]]}.",
                )
            if operation != "update":
                raise ValueError("operation must be create, update or delete")
            _require(target, "name")
            if not fields:
                raise ValueError(f"at least one {doctype} field is required for update")
            before = _data(frappe.get_document(doctype, target))
            key = write_plans.plan_key(doctype, target)
            return _course_plan_finish(
                action=action, title=f"Xem trước cập nhật {doctype}",
                summary=f"Xem trước cập nhật {target}.",
                args=a, operation=operation,
                items=_preview_field_items(doctype, target, before, fields),
                changes=[_change(doctype, target, "update")],
                expected_modified={key: _course_plan_expected(before)},
                requires_edit_review=(doctype == "LMS Programming Exercise" and "test_cases" in fields),
                preview=f"Cập nhật {doctype} {target}.",
            )
        if operation == "create":
            for field in required:
                _require(fields.get(field), field)
            result = frappe.create_document(doctype, fields)
            name = _name(result, fields[required[0]])
            return action_result(action, f"Đã tạo {doctype}", f"Đã tạo {name}.", [_change(doctype, name, "create")], undo={"op": "delete", "doctype": doctype, "name": name})
        if operation != "update":
            raise ValueError("operation must be create, update or delete")
        _require(target, "name")
        if not fields:
            raise ValueError(f"at least one {doctype} field is required for update")
        before = _data(frappe.get_document(doctype, target))
        frappe.update_document(doctype, target, fields)
        return action_result(action, f"Đã cập nhật {doctype}", f"Đã cập nhật {target}.", [_change(doctype, target, "update")], undo={"op": "restore", "doctype": doctype, "name": target, "fields": {key: before.get(key) for key in fields}})

    def manage_assignment(a: dict):
        return _simple_manager(a, doctype="LMS Assignment", action="manage_assignment", allowed={"title", "question", "type", "course", "grade_assignment", "show_answer", "answer"}, required=("title", "question", "type"))

    _register_mutation(reg, "manage_assignment", "Tạo, sửa hoặc xóa assignment của khóa học, gồm đề bài, loại nộp, đáp án và chế độ chấm.", {
        "type": "object", "properties": {"operation": {"type": "string", "enum": ["create", "update"]}, "name": {"type": "string"}, "title": {"type": "string"}, "question": {"type": "string"}, "type": {"type": "string", "enum": ["Document", "PDF", "URL", "Image", "Text"]}, "course": {"type": "string"}, "grade_assignment": {"type": "integer", "enum": [0, 1]}, "show_answer": {"type": "integer", "enum": [0, 1]}, "answer": {"type": "string"}, "dry_run": {"type": "boolean", "description": "Preview-only plan; performs reads and returns pending_approval without Frappe writes"}}, "required": ["operation"],
    }, manage_assignment)

    def manage_programming_exercise(a: dict):
        return _simple_manager(a, doctype="LMS Programming Exercise", action="manage_programming_exercise", allowed={"title", "problem_statement", "language", "test_cases"}, required=("title", "problem_statement", "language"))

    _register_mutation(reg, "manage_programming_exercise", "Tạo, sửa hoặc xóa bài lập trình với ngôn ngữ và test case đầu vào/đầu ra.", {
        "type": "object", "properties": {"operation": {"type": "string", "enum": ["create", "update"]}, "name": {"type": "string"}, "title": {"type": "string"}, "problem_statement": {"type": "string"}, "language": {"type": "string", "enum": ["Python", "JavaScript", "Rust", "Go"]}, "test_cases": {"type": "array", "items": {"type": "object", "properties": {"input": {"type": "string"}, "expected_output": {"type": "string"}}, "required": ["expected_output"]}}, "dry_run": {"type": "boolean", "description": "Preview-only plan; performs reads and returns pending_approval without Frappe writes"}}, "required": ["operation"],
    }, manage_programming_exercise)
