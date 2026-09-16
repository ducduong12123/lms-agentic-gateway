import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.runtime.tool_registry import ToolRegistry
from gateway.tools import verbs_course_authoring, verbs_teacher


class FakeFrappe:
    def __init__(self):
        self.docs = {
            "Notification Log": [],
            "LMS Live Class": [],
            "LMS Course": [{"name": "PY-101", "title": "Python", "chapters": [{"chapter": "CH-1"}]}],
            "Course Chapter": [{"name": "CH-1", "course": "PY-101", "title": "Basics", "lessons": []}],
            "Course Lesson": [],
            "LMS Quiz": [],
            "LMS Question": [],
            "LMS Assignment": [],
            "LMS Programming Exercise": [],
        }
        self.counter = 0

    def list_documents(self, doctype, filters=None, fields=None, limit=20):
        filters = filters or {}
        rows = [row for row in self.docs.get(doctype, []) if all(str(row.get(k)) == str(v) for k, v in filters.items())]
        return {"data": [dict(row) for row in rows[:limit]]}
    def create_document(self, doctype, doc):
        self.counter += 1
        row = {"name": f"{doctype}-{self.counter}", **doc}
        self.docs.setdefault(doctype, []).append(row)
        return {"data": row}

    def get_document(self, doctype, name):
        row = next((item for item in self.docs.get(doctype, []) if item["name"] == name), {})
        return {"data": dict(row)}

    def update_document(self, doctype, name, doc):
        for row in self.docs.get(doctype, []):
            if row["name"] == name:
                row.update(doc)
                return {"data": dict(row)}
        raise ValueError("missing document")

    def delete_document(self, doctype, name):
        rows = self.docs.get(doctype, [])
        self.docs[doctype] = [row for row in rows if row["name"] != name]
        return {"data": {"name": name}}

    def call_method(self, dotted, **kwargs):
        if dotted.endswith("delete_lesson"):
            self.delete_document("Course Lesson", kwargs["lesson"])
            chapter = self.get_document("Course Chapter", kwargs["chapter"])["data"]
            chapter["lessons"] = [row for row in chapter.get("lessons", []) if row.get("lesson") != kwargs["lesson"]]
            self.update_document("Course Chapter", kwargs["chapter"], {"lessons": chapter["lessons"]})
        elif dotted.endswith("delete_chapter"):
            self.delete_document("Course Chapter", kwargs["chapter"])
            for course in self.docs["LMS Course"]:
                course["chapters"] = [row for row in course.get("chapters", []) if row.get("chapter") != kwargs["chapter"]]
        elif dotted.endswith("delete_course"):
            self.delete_document("LMS Course", kwargs["course"])
        return {"message": True}


def test_teacher_mutations_are_approval_gated_and_return_actions():
    frappe = FakeFrappe()
    registry = ToolRegistry()
    verbs_teacher.register(registry, frappe)

    for name in ("message_students", "create_live_class", "publish_lesson_draft"):
        assert registry.get(name).needs_approval
        assert registry.get(name).approval_roles == {"teacher", "admin"}

    message = registry.get("message_students").func({
        "member": "teacher@example.com", "targets": ["a@example.com", "b@example.com"],
        "subject": "Ôn tập", "message": "Hãy xem lại concept.",
    })
    live = registry.get("create_live_class").func({
        "member": "teacher@example.com", "batch": "B-1", "title": "Viva",
        "when": "2026-02-01T09:00:00", "duration": 45,
    })
    draft = registry.get("publish_lesson_draft").func({
        "course": "PY-101", "chapter": "CH-1", "title": "Loops", "body": "Draft body",
    })
    assert message["kind"] == live["kind"] == draft["kind"] == "action"
    assert len(message["changes"]) == 2
    assert frappe.docs["LMS Live Class"][0]["batch_name"] == "B-1"
    assert live["undo"]["op"] == "delete"
    repeated = registry.get("message_students").func({
        "member": "teacher@example.com", "targets": ["a@example.com", "b@example.com"],
        "subject": "Ôn tập", "message": "Hãy xem lại concept.",
    })
    assert repeated["status"] == "noop"
    assert draft["view"] == {"type": "course", "course": "PY-101"}


def test_complete_course_authoring_workflow_keeps_parent_references():
    frappe = FakeFrappe()
    registry = ToolRegistry()
    verbs_course_authoring.register(registry, frappe)
    mutation_names = {
        "manage_course", "manage_chapter", "manage_lesson", "manage_lesson_block",
        "manage_quiz", "manage_assignment", "manage_programming_exercise",
    }
    for name in mutation_names:
        assert registry.get(name).needs_approval
        assert registry.get(name).approval_roles == {"teacher", "admin"}

    course_result = registry.get("manage_course").func({
        "operation": "create", "title": "AI Foundations",
        "description": "Full course", "short_introduction": "Start here",
    })
    course = course_result["changes"][0]["name"]
    chapter_result = registry.get("manage_chapter").func({
        "operation": "create", "course": course, "title": "Module 1",
    })
    chapter = chapter_result["changes"][0]["name"]
    lesson_result = registry.get("manage_lesson").func({
        "operation": "create", "course": course, "chapter": chapter,
        "title": "Prompting", "body": "# Prompting",
    })
    lesson = lesson_result["changes"][0]["name"]
    quiz_result = registry.get("manage_quiz").func({
        "operation": "create", "title": "Check 1", "course": course, "lesson": lesson,
        "questions": [{
            "question": "Best prompt?", "type": "Choices", "marks": 2,
            "options": [{"text": "Specific", "correct": 1}, {"text": "Vague", "correct": 0}],
        }],
    })
    assignment_result = registry.get("manage_assignment").func({
        "operation": "create", "title": "Write a prompt", "question": "Submit it",
        "type": "Text", "course": course,
    })
    exercise_result = registry.get("manage_programming_exercise").func({
        "operation": "create", "title": "Echo", "problem_statement": "Echo input",
        "language": "Python", "test_cases": [{"input": "a", "expected_output": "a"}],
    })
    exercise = exercise_result["changes"][0]["name"]
    registry.get("manage_lesson_block").func({
        "operation": "attach", "lesson": lesson, "block_type": "program", "resource": exercise,
    })

    course_doc = frappe.get_document("LMS Course", course)["data"]
    chapter_doc = frappe.get_document("Course Chapter", chapter)["data"]
    state = registry.get("get_course_authoring_state").func({"course": course})
    assert course_doc["chapters"] == [{"chapter": chapter}]
    assert chapter_doc["lessons"] == [{"lesson": lesson}]
    assert state["chapters"][0]["lesson_documents"][0]["body"] == "# Prompting"
    blocks = json.loads(frappe.get_document("Course Lesson", lesson)["data"]["content"])["blocks"]
    assert blocks == [
        {"type": "markdown", "data": {"text": "# Prompting"}},
        {"type": "program", "data": {"exercise": exercise}},
    ]
    assert len(frappe.docs["LMS Question"]) == 1
    assert frappe.docs["LMS Quiz"][0]["questions"][0]["marks"] == 2
    assert quiz_result["kind"] == assignment_result["kind"] == exercise_result["kind"] == "action"


def test_teacher_gap_analysis_is_a_whitelisted_client_directive(monkeypatch):
    monkeypatch.setattr(verbs_teacher.features, "risk_overview", lambda course, limit: [{"weak_concepts": [{"label": "Closures"}]}])
    registry = ToolRegistry()
    verbs_teacher.register(registry, FakeFrappe())

    result = registry.get("analyze_course_gaps").func({"course": "PY-101"})

    assert result == {
        "kind": "client",
        "op": "render_view",
        "spec": {
            "type": "diff",
            "doctype": "Course",
            "name": "PY-101",
            "before": "Phân tích gap đang dùng dữ liệu mastery hiện có.",
            "after": "- Bổ sung hướng dẫn cho Closures",
        },
    }
