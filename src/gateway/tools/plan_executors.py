"""Tier-1 plan executors: selected write-plan items with optimistic concurrency."""
from __future__ import annotations

import json

from gateway.runtime import plan_apply, write_plans
from gateway.tools import verbs_course_authoring as authoring
from gateway.tools.envelope import action_result


def _before_for_apply(frappe, expected_modified: dict) -> dict[str, dict]:
    before: dict[str, dict] = {}
    for key in dict(expected_modified or {}):
        if ":" not in str(key or ""):
            continue
        doctype, _, name = str(key).partition(":")
        if not doctype or not name:
            continue
        document = authoring.snapshot_document(frappe, doctype, name)
        before[write_plans.plan_key(doctype, name)] = dict(document)
    return before


def _scoped_fields(payload: dict, allowed: set[str]) -> dict:
    return {key: value for key, value in dict(payload or {}).items() if key in allowed}


def apply_manage_course_create(frappe, plan: dict, merged: dict[str, dict]) -> dict:
    fields: dict = {}
    for payload in merged.values():
        entry_fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else payload
        if isinstance(entry_fields, dict):
            fields.update(_scoped_fields(entry_fields, authoring.COURSE_FIELD_SET))
    if not fields:
        raise ValueError("no approved course fields to apply")
    fields = authoring.normalize_course_fields(fields)
    for field in ("title", "description", "short_introduction"):
        authoring.require_text(fields.get(field), field)
    result = frappe.create_document("LMS Course", fields)
    name = authoring.created_name(result, str(fields.get("title") or ""))
    changes = [{"doctype": "LMS Course", "name": name, "op": "create"}]
    return action_result(
        "manage_course", "Đã tạo khóa học", f"Đã tạo khóa học {fields.get('title', '')}.",
        changes, undo={"op": "delete", "doctype": "LMS Course", "name": name},
        view={"type": "course", "course": name},
    )


def apply_manage_course_update(frappe, plan: dict, merged: dict[str, dict]) -> dict:
    fields: dict = {}
    for item_id, payload in merged.items():
        entry_fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else payload
        if isinstance(entry_fields, dict):
            fields.update(_scoped_fields(entry_fields, authoring.COURSE_FIELD_SET))
    if not fields:
        raise ValueError("no approved course fields to apply")
    course = str((plan.get("args") or {}).get("course") or next(iter(merged.values())).get("name") if merged else "")
    if not course:
        raise ValueError("course is required")
    course = authoring.require_text(course, "course")
    fields = authoring.normalize_course_fields(fields)
    before = _before_for_apply(frappe, plan.get("expected_modified") or {})
    plan_apply.check_expected_modified(frappe, plan.get("expected_modified") or {})
    frappe.update_document("LMS Course", course, fields)
    changes = [
        {"doctype": "LMS Course", "name": course, "op": "update", "field": key}
        for key in fields
    ]
    undo = plan_apply.collect_write_undo(changes, before)
    return action_result(
        "manage_course", "Đã cập nhật khóa học", f"Đã cập nhật {len(fields)} trường của {course}.",
        changes, undo=undo, view={"type": "course", "course": course},
    )

def apply_manage_chapter_create(frappe, plan: dict, merged: dict[str, dict]) -> dict:
    course = str((plan.get("args") or {}).get("course") or "")
    title = str((plan.get("args") or {}).get("title") or "")
    for payload in merged.values():
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        if isinstance(fields, dict):
            course = str(fields.get("course") or payload.get("course") or course)
            title = str(fields.get("title") or payload.get("title") or title)
        course = str(payload.get("course") or course)
        title = str(payload.get("title") or title)
    course = authoring.require_text(course, "course")
    title = authoring.require_text(title, "title")
    before = _before_for_apply(frappe, plan.get("expected_modified") or {})
    plan_apply.check_expected_modified(frappe, plan.get("expected_modified") or {})
    created = frappe.create_document("Course Chapter", {"course": course, "title": title})
    chapter = authoring.created_name(created, title)
    document = authoring.snapshot_document(frappe, "LMS Course", course)
    chapters = authoring.child_names(document, "chapters", "chapter")
    if chapter not in chapters:
        authoring.replace_child_table(frappe, "LMS Course", course, "chapters", "chapter", [*chapters, chapter])
    changes = [
        {"doctype": "Course Chapter", "name": chapter, "op": "create"},
        {"doctype": "LMS Course", "name": course, "op": "update"},
    ]
    return action_result(
        "manage_chapter", "Đã tạo chương", f"Đã thêm {title} vào {course}.",
        changes, undo={"op": "delete", "doctype": "Course Chapter", "name": chapter},
        view={"type": "course", "course": course},
    )


def apply_manage_lesson_create(frappe, plan: dict, merged: dict[str, dict]) -> dict:
    fields: dict = {}
    course = str((plan.get("args") or {}).get("course") or "")
    chapter = str((plan.get("args") or {}).get("chapter") or "")
    title = str((plan.get("args") or {}).get("title") or "")
    for payload in merged.values():
        entry_fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else payload
        if isinstance(entry_fields, dict):
            fields.update(_scoped_fields(entry_fields, authoring.LESSON_FIELD_SET))
        course = str(payload.get("course") or course)
        chapter = str(payload.get("chapter") or chapter)
        title = str(payload.get("title") or title)
    course = authoring.require_text(course, "course")
    chapter = authoring.require_text(chapter, "chapter")
    title = authoring.require_text(title, "title")
    fields = {"course": course, "chapter": chapter, "title": title, **fields}
    before = _before_for_apply(frappe, plan.get("expected_modified") or {})
    plan_apply.check_expected_modified(frappe, plan.get("expected_modified") or {})
    created = frappe.create_document("Course Lesson", fields)
    lesson = authoring.created_name(created, title)
    authoring.ensure_lesson_reference(frappe, chapter, lesson)
    changes = [
        {"doctype": "Course Lesson", "name": lesson, "op": "create"},
        {"doctype": "Course Chapter", "name": chapter, "op": "update"},
    ]
    return action_result(
        "manage_lesson", "Đã tạo bài học", f"Đã thêm {title} vào {chapter}.",
        changes, undo={"op": "delete", "doctype": "Course Lesson", "name": lesson},
        view={"type": "course", "course": course},
    )


def apply_manage_quiz_create(frappe, plan: dict, merged: dict[str, dict]) -> dict:
    fields: dict = {}
    new_questions: list[dict] = []
    for payload in merged.values():
        entry_fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        fields.update(_scoped_fields(entry_fields, authoring.QUIZ_FIELD_SET))
        document = payload.get("document")
        if isinstance(document, dict) and document:
            marks = payload.get("marks", 1)
            try:
                marks = max(1, int(marks if not isinstance(marks, dict) else 1))
            except (TypeError, ValueError):
                marks = 1
            new_questions.append({"document": dict(document), "marks": marks})
    authoring.require_text(fields.get("title"), "title")
    created_questions = authoring.create_quiz_questions(frappe, new_questions) if new_questions else []
    payload_questions = [{"question": item, "marks": marks["marks"]} for item, marks in zip(created_questions, new_questions)]
    try:
        result = frappe.create_document("LMS Quiz", {**fields, "questions": payload_questions} if payload_questions else fields)
    except Exception:
        for question in reversed(created_questions):
            try:
                frappe.delete_document("LMS Question", question)
            except Exception:
                pass
        raise
    quiz = authoring.created_name(result, str(fields.get("title") or ""))
    changes = [{"doctype": "LMS Quiz", "name": quiz, "op": "create"}] + [
        {"doctype": "LMS Question", "name": item, "op": "create"} for item in created_questions
    ]
    return action_result(
        "manage_quiz", "Đã tạo quiz", f"Đã tạo quiz {fields.get('title', '')} với {len(created_questions)} câu hỏi.",
        changes, undo={
            "op": "restore_many",
            "restores": [{"doctype": "LMS Quiz", "name": quiz, "delete": True}] + [
                {"doctype": "LMS Question", "name": item, "delete": True} for item in created_questions
            ],
        },
    )


def apply_simple_create(frappe, *, action: str, doctype: str, allowed: set[str], required: tuple[str, ...], plan: dict, merged: dict[str, dict]) -> dict:
    fields: dict = {}
    for payload in merged.values():
        entry_fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else payload
        if isinstance(entry_fields, dict):
            fields.update(_scoped_fields(entry_fields, allowed))
    for field in required:
        authoring.require_text(fields.get(field), field)
    result = frappe.create_document(doctype, fields)
    name = authoring.created_name(result, str(fields.get(required[0]) or ""))
    changes = [{"doctype": doctype, "name": name, "op": "create"}]
    return action_result(
        action, f"Đã tạo {doctype}", f"Đã tạo {name}.",
        changes, undo={"op": "delete", "doctype": doctype, "name": name},
    )

def apply_manage_chapter_update(frappe, plan: dict, merged: dict[str, dict]) -> dict:
    title = ""
    chapter = str((plan.get("args") or {}).get("chapter") or "")
    for payload in merged.values():
        candidate = payload.get("fields", {}).get("title") if isinstance(payload.get("fields"), dict) else payload.get("title")
        if candidate:
            title = str(candidate)
        if not chapter and payload.get("name"):
            chapter = str(payload.get("name"))
    chapter = authoring.require_text(chapter, "chapter")
    title = authoring.require_text(title, "title")
    before = _before_for_apply(frappe, plan.get("expected_modified") or {})
    plan_apply.check_expected_modified(frappe, plan.get("expected_modified") or {})
    frappe.update_document("Course Chapter", chapter, {"title": title})
    changes = [{"doctype": "Course Chapter", "name": chapter, "op": "update", "field": "title"}]
    undo = plan_apply.collect_write_undo(changes, before)
    return action_result(
        "manage_chapter", "Đã cập nhật chương", f"Đã đổi tiêu đề chương {chapter}.",
        changes, undo=undo,
    )


def apply_manage_chapter_reorder(frappe, plan: dict, merged: dict[str, dict]) -> dict:
    ordered: list[str] = []
    for payload in merged.values():
        candidate = payload.get("ordered_chapters") or payload.get("fields", {}).get("ordered_chapters")
        if isinstance(candidate, list) and candidate:
            ordered = [str(item) for item in candidate]
    course = str((plan.get("args") or {}).get("course") or "")
    course = authoring.require_text(course, "course")
    if not ordered:
        raise ValueError("ordered_chapters is required")
    before = _before_for_apply(frappe, plan.get("expected_modified") or {})
    plan_apply.check_expected_modified(frappe, plan.get("expected_modified") or {})
    current = authoring.child_names(authoring.snapshot_document(frappe, "LMS Course", course), "chapters", "chapter")
    if len(ordered) != len(set(ordered)) or set(ordered) != set(current):
        raise ValueError("ordered_chapters must contain every current chapter exactly once")
    authoring.replace_child_table(frappe, "LMS Course", course, "chapters", "chapter", ordered)
    changes = [{"doctype": "LMS Course", "name": course, "op": "reorder"}]
    return action_result(
        "manage_chapter", "Đã sắp xếp chương", f"Đã sắp xếp {len(ordered)} chương trong {course}.",
        changes, undo={
            "op": "restore", "doctype": "LMS Course", "name": course,
            "fields": {"chapters": [{"chapter": item} for item in current]},
        },
        view={"type": "course", "course": course},
    )


def apply_manage_lesson_update(frappe, plan: dict, merged: dict[str, dict]) -> dict:
    fields: dict = {}
    lesson = str((plan.get("args") or {}).get("lesson") or "")
    for payload in merged.values():
        entry_fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else payload
        if isinstance(entry_fields, dict):
            fields.update(_scoped_fields(entry_fields, authoring.LESSON_FIELD_SET))
        if not lesson and payload.get("name"):
            lesson = str(payload.get("name"))
    lesson = authoring.require_text(lesson, "lesson")
    if not fields:
        raise ValueError("at least one lesson field is required for update")
    before = _before_for_apply(frappe, plan.get("expected_modified") or {})
    plan_apply.check_expected_modified(frappe, plan.get("expected_modified") or {})
    frappe.update_document("Course Lesson", lesson, fields)
    course = str(before.get(write_plans.plan_key("Course Lesson", lesson), {}).get("course") or "")
    changes = [
        {"doctype": "Course Lesson", "name": lesson, "op": "update", "field": key}
        for key in fields
    ]
    undo = plan_apply.collect_write_undo(changes, before)
    view = {"type": "course", "course": course} if course else None
    return action_result(
        "manage_lesson", "Đã cập nhật bài học", f"Đã cập nhật {len(fields)} trường của {lesson}.",
        changes, undo=undo, view=view,
    )


def apply_manage_lesson_reorder(frappe, plan: dict, merged: dict[str, dict]) -> dict:
    ordered: list[str] = []
    chapter = str((plan.get("args") or {}).get("chapter") or "")
    for payload in merged.values():
        candidate = payload.get("ordered_lessons") or payload.get("fields", {}).get("ordered_lessons")
        if isinstance(candidate, list) and candidate:
            ordered = [str(item) for item in candidate]
        if not chapter and payload.get("chapter"):
            chapter = str(payload.get("chapter"))
    chapter = authoring.require_text(chapter, "chapter")
    if not ordered:
        raise ValueError("ordered_lessons is required")
    before = _before_for_apply(frappe, plan.get("expected_modified") or {})
    plan_apply.check_expected_modified(frappe, plan.get("expected_modified") or {})
    current = authoring.child_names(authoring.snapshot_document(frappe, "Course Chapter", chapter), "lessons", "lesson")
    if len(ordered) != len(set(ordered)) or set(ordered) != set(current):
        raise ValueError("ordered_lessons must contain every current lesson exactly once")
    authoring.replace_child_table(frappe, "Course Chapter", chapter, "lessons", "lesson", ordered)
    changes = [{"doctype": "Course Chapter", "name": chapter, "op": "reorder"}]
    return action_result(
        "manage_lesson", "Đã sắp xếp bài học", f"Đã sắp xếp {len(ordered)} bài trong {chapter}.",
        changes, undo={
            "op": "restore", "doctype": "Course Chapter", "name": chapter,
            "fields": {"lessons": [{"lesson": item} for item in current]},
        },
    )


def apply_manage_lesson_block(frappe, plan: dict, merged: dict[str, dict]) -> dict:
    contents = [
        str(payload.get("fields", {}).get("content") or "")
        for payload in merged.values()
        if isinstance(payload.get("fields"), dict) and payload.get("fields", {}).get("content")
    ]
    if not contents:
        raise ValueError("no approved lesson content to apply")
    new_content = contents[-1]
    try:
        parsed = json.loads(new_content)
    except (TypeError, ValueError) as exc:
        raise ValueError("approved lesson content is not valid EditorJS JSON") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("blocks"), list):
        raise ValueError("approved lesson content must be an EditorJS object with a blocks array")
    lesson = str((plan.get("args") or {}).get("lesson") or "")
    lesson = authoring.require_text(lesson, "lesson")
    document = authoring.snapshot_document(frappe, "Course Lesson", lesson)
    before = {write_plans.plan_key("Course Lesson", lesson): dict(document)}
    plan_apply.check_expected_modified(frappe, plan.get("expected_modified") or {})
    frappe.update_document("Course Lesson", lesson, {"content": new_content})
    changes = [{"doctype": "Course Lesson", "name": lesson, "op": "update", "field": "content"}]
    undo = plan_apply.collect_write_undo(changes, before)
    course = str(document.get("course") or "")
    return action_result(
        "manage_lesson_block", "Đã cập nhật nội dung bài học",
        f"Đã cập nhật nội dung EditorJS trong {lesson}.",
        changes, undo=undo, view={"type": "course", "course": course} if course else None,
    )


def apply_manage_quiz_update(frappe, plan: dict, merged: dict[str, dict]) -> dict:
    fields: dict = {}
    new_questions: list[dict] = []
    quiz = str((plan.get("args") or {}).get("quiz") or "")
    for payload in merged.values():
        entry_fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        fields.update(_scoped_fields(entry_fields, authoring.QUIZ_FIELD_SET))
        document = payload.get("document")
        if isinstance(document, dict) and document:
            marks = payload.get("marks", 1)
            try:
                marks = max(1, int(marks if not isinstance(marks, dict) else 1))
            except (TypeError, ValueError):
                marks = 1
            new_questions.append({"document": dict(document), "marks": marks})
        if not quiz and payload.get("name"):
            quiz = str(payload.get("name"))
        if not quiz and payload.get("quiz"):
            quiz = str(payload.get("quiz"))
    quiz = authoring.require_text(quiz, "quiz")
    if not fields and not new_questions:
        raise ValueError("at least one quiz field or questions is required for update")
    before = _before_for_apply(frappe, plan.get("expected_modified") or {})
    plan_apply.check_expected_modified(frappe, plan.get("expected_modified") or {})
    created_questions = authoring.create_quiz_questions(frappe, new_questions) if new_questions else []
    if fields:
        frappe.update_document("LMS Quiz", quiz, fields)
    changes = [
        {"doctype": "LMS Quiz", "name": quiz, "op": "update", "field": key}
        for key in fields
    ] + [
        {"doctype": "LMS Question", "name": item, "op": "create"}
        for item in created_questions
    ]
    undo = plan_apply.collect_write_undo(changes, before)
    if created_questions and (not undo or undo.get("op") != "restore_many"):
        undo = {
            "op": "restore_many",
            "restores": ([{
                "doctype": "LMS Quiz", "name": quiz,
                "fields": {key: before.get(write_plans.plan_key("LMS Quiz", quiz), {}).get(key) for key in fields},
            }] if fields else []) + [
                {"doctype": "LMS Question", "name": item, "delete": True}
                for item in created_questions
            ],
        }
    return action_result(
        "manage_quiz", "Đã cập nhật quiz", f"Đã cập nhật quiz {quiz}.",
        changes, undo=undo,
    )


def apply_simple_update(frappe, *, action: str, doctype: str, allowed: set[str], plan: dict, merged: dict[str, dict]) -> dict:
    fields: dict = {}
    target = str((plan.get("args") or {}).get("name") or "")
    for payload in merged.values():
        entry_fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else payload
        if isinstance(entry_fields, dict):
            fields.update(_scoped_fields(entry_fields, allowed))
        if not target and payload.get("name"):
            target = str(payload.get("name"))
    target = authoring.require_text(target, "name")
    if not fields:
        raise ValueError(f"at least one {doctype} field is required for update")
    before = _before_for_apply(frappe, plan.get("expected_modified") or {})
    plan_apply.check_expected_modified(frappe, plan.get("expected_modified") or {})
    frappe.update_document(doctype, target, fields)
    changes = [
        {"doctype": doctype, "name": target, "op": "update", "field": key}
        for key in fields
    ]
    undo = plan_apply.collect_write_undo(changes, before)
    return action_result(
        action, f"Đã cập nhật {doctype}", f"Đã cập nhật {target}.",
        changes, undo=undo,
    )
