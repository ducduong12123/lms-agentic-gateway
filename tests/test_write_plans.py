import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.runtime import write_plans
from gateway.runtime.tool_registry import ToolRegistry
from gateway.tools import verbs_course_authoring


class FakeFrappe:
    """FakeFrappe pattern from test_verbs_teacher with write counters."""

    def __init__(self):
        self.docs = {
            "LMS Course": [{
                "name": "PY-101", "title": "Python", "modified": "v1",
                "chapters": [{"chapter": "CH-1"}],
            }],
            "Course Chapter": [{
                "name": "CH-1", "course": "PY-101", "title": "Basics",
                "modified": "v1", "lessons": [{"lesson": "LS-1"}],
            }],
            "Course Lesson": [{
                "name": "LS-1", "course": "PY-101", "chapter": "CH-1",
                "title": "Intro", "body": "# hi", "modified": "v1",
                "content": json.dumps({"blocks": []}),
            }],
        }
        self.writes = {"create": 0, "update": 0, "delete": 0, "call": 0}

    def list_documents(self, doctype, filters=None, fields=None, limit=20):
        filters = filters or {}
        rows = [
            row for row in self.docs.get(doctype, [])
            if all(str(row.get(k)) == str(v) for k, v in filters.items())
        ]
        return {"data": [dict(row) for row in rows[:limit]]}

    def create_document(self, doctype, doc):
        self.writes["create"] += 1
        row = {"name": f"{doctype}-N", **doc}
        self.docs.setdefault(doctype, []).append(row)
        return {"data": row}

    def get_document(self, doctype, name):
        row = next((item for item in self.docs.get(doctype, []) if item["name"] == name), {})
        return {"data": dict(row)}

    def update_document(self, doctype, name, doc):
        self.writes["update"] += 1
        for row in self.docs.get(doctype, []):
            if row["name"] == name:
                row.update(doc)
                return {"data": dict(row)}
        raise ValueError("missing document")

    def delete_document(self, doctype, name):
        self.writes["delete"] += 1
        self.docs[doctype] = [row for row in self.docs.get(doctype, []) if row["name"] != name]
        return {"data": {"name": name}}

    def call_method(self, dotted, **kwargs):
        self.writes["call"] += 1
        return {"message": True}


def _registry(frappe):
    registry = ToolRegistry()
    verbs_course_authoring.register(registry, frappe)
    return registry


def _isolate_db(monkeypatch, tmp_path):
    monkeypatch.setattr(write_plans, "DB", tmp_path / "plans.db")


def _assert_no_writes(frappe):
    assert frappe.writes == {"create": 0, "update": 0, "delete": 0, "call": 0}


def test_every_write_operation_dry_run_performs_zero_writes(tmp_path, monkeypatch):
    _isolate_db(monkeypatch, tmp_path)
    frappe = FakeFrappe()
    registry = _registry(frappe)
    calls = [
        ("manage_course", {"operation": "create", "title": "T", "description": "D", "short_introduction": "S", "dry_run": True}),
        ("manage_course", {"operation": "update", "course": "PY-101", "title": "T2", "dry_run": True}),
        ("manage_chapter", {"operation": "create", "course": "PY-101", "title": "CH", "dry_run": True}),
        ("manage_chapter", {"operation": "update", "chapter": "CH-1", "title": "CH2", "dry_run": True}),
        ("manage_chapter", {"operation": "reorder", "course": "PY-101", "ordered_chapters": ["CH-1"], "dry_run": True}),
        ("manage_lesson", {"operation": "create", "course": "PY-101", "chapter": "CH-1", "title": "LS", "dry_run": True}),
        ("manage_lesson", {"operation": "update", "lesson": "LS-1", "title": "LS2", "dry_run": True}),
        ("manage_lesson", {"operation": "reorder", "chapter": "CH-1", "ordered_lessons": ["LS-1"], "dry_run": True}),
        ("manage_lesson_block", {"operation": "attach", "lesson": "LS-1", "block_type": "quiz", "resource": "Q-9", "dry_run": True}),
        ("manage_quiz", {"operation": "create", "title": "Q", "questions": [{"question": "QQ?", "type": "Choices", "marks": 1, "options": [{"text": "A"}]}], "dry_run": True}),
        ("manage_quiz", {"operation": "update", "quiz": "QZ-1", "title": "Q2", "dry_run": True}),
        ("manage_assignment", {"operation": "create", "title": "A", "question": "Q?", "type": "Text", "dry_run": True}),
        ("manage_assignment", {"operation": "update", "name": "AS-1", "title": "A2", "dry_run": True}),
        ("manage_programming_exercise", {"operation": "create", "title": "E", "problem_statement": "P", "language": "Python", "dry_run": True}),
        ("manage_programming_exercise", {"operation": "update", "name": "EX-1", "title": "E2", "dry_run": True}),
    ]
    for tool_name, args in calls:
        frappe.writes = {"create": 0, "update": 0, "delete": 0, "call": 0}
        result = registry.get(tool_name).func(dict(args))
        assert result.get("status") == "pending_approval", (tool_name, args.get("operation"), result)
        assert result.get("plan_id"), (tool_name, args.get("operation"))
        _assert_no_writes(frappe)


def test_delete_operations_are_not_offered_by_tool_schemas():
    frappe = FakeFrappe()
    registry = _registry(frappe)
    for tool_name in ("manage_course", "manage_chapter", "manage_lesson", "manage_quiz", "manage_assignment", "manage_programming_exercise"):
        operations = registry.get(tool_name).parameters["properties"]["operation"]["enum"]
        assert "delete" not in operations, tool_name
def test_manage_course_update_preview(tmp_path, monkeypatch):
    _isolate_db(monkeypatch, tmp_path)
    frappe = FakeFrappe()
    registry = _registry(frappe)
    result = registry.get("manage_course").func({
        "operation": "update", "course": "PY-101", "title": "Python 2",
        "dry_run": True, "member": "teacher@example.com",
    })
    _assert_no_writes(frappe)
    assert result["status"] == "pending_approval"
    assert result["plan_id"]
    assert result["items"]
    assert result["reversibility"] in {"reversible", "compensating", "irreversible"}
    stored = write_plans.get_plan(result["plan_id"], tmp_path / "plans.db")
    assert stored is not None
    assert stored["expected_modified"] == {"LMS Course:PY-101": "v1"}
    assert frappe.get_document("LMS Course", "PY-101")["data"]["title"] == "Python"


def test_manage_course_delete_preview(tmp_path, monkeypatch):
    _isolate_db(monkeypatch, tmp_path)
    frappe = FakeFrappe()
    registry = _registry(frappe)
    result = registry.get("manage_course").func({
        "operation": "delete", "course": "PY-101",
        "dry_run": True, "member": "teacher@example.com",
    })
    _assert_no_writes(frappe)
    assert result["status"] == "pending_approval"
    assert result["plan_id"]
    assert result["typed_confirm"] == "PY-101"
    stored = write_plans.get_plan(result["plan_id"], tmp_path / "plans.db")
    assert stored["expected_modified"] == {"LMS Course:PY-101": "v1"}
    assert frappe.get_document("LMS Course", "PY-101")["data"]


def test_manage_chapter_update_and_delete_preview(tmp_path, monkeypatch):
    _isolate_db(monkeypatch, tmp_path)
    frappe = FakeFrappe()
    registry = _registry(frappe)
    updated = registry.get("manage_chapter").func({
        "operation": "update", "chapter": "CH-1", "title": "New",
        "dry_run": True, "member": "teacher@example.com",
    })
    deleted = registry.get("manage_chapter").func({
        "operation": "delete", "chapter": "CH-1",
        "dry_run": True, "member": "teacher@example.com",
    })
    _assert_no_writes(frappe)
    for result in (updated, deleted):
        assert result["status"] == "pending_approval"
        assert result["plan_id"] and result["items"]
    stored = write_plans.get_plan(updated["plan_id"], tmp_path / "plans.db")
    assert stored["expected_modified"] == {"Course Chapter:CH-1": "v1"}
    assert deleted["typed_confirm"] == "CH-1"


def test_manage_lesson_update_and_delete_preview(tmp_path, monkeypatch):
    _isolate_db(monkeypatch, tmp_path)
    frappe = FakeFrappe()
    registry = _registry(frappe)
    updated = registry.get("manage_lesson").func({
        "operation": "update", "lesson": "LS-1", "title": "Intro 2",
        "dry_run": True, "member": "teacher@example.com",
    })
    deleted = registry.get("manage_lesson").func({
        "operation": "delete", "lesson": "LS-1", "chapter": "CH-1",
        "dry_run": True, "member": "teacher@example.com",
    })
    _assert_no_writes(frappe)
    assert updated["status"] == "pending_approval" == deleted["status"]
    stored_update = write_plans.get_plan(updated["plan_id"], tmp_path / "plans.db")
    assert stored_update["expected_modified"] == {"Course Lesson:LS-1": "v1"}
    stored_delete = write_plans.get_plan(deleted["plan_id"], tmp_path / "plans.db")
    assert stored_delete["expected_modified"] == {
        "Course Lesson:LS-1": "v1", "Course Chapter:CH-1": "v1",
    }


def test_manage_lesson_block_attach_detach_preview(tmp_path, monkeypatch):
    _isolate_db(monkeypatch, tmp_path)
    frappe = FakeFrappe()
    registry = _registry(frappe)
    attached = registry.get("manage_lesson_block").func({
        "operation": "attach", "lesson": "LS-1", "block_type": "quiz",
        "resource": "Q-1", "dry_run": True, "member": "teacher@example.com",
    })
    _assert_no_writes(frappe)
    assert attached["status"] == "pending_approval"
    assert attached["plan_id"] and attached["items"]
    assert attached["reversibility"] == "reversible"
    stored = write_plans.get_plan(attached["plan_id"], tmp_path / "plans.db")
    assert stored["expected_modified"] == {"Course Lesson:LS-1": "v1"}
    after = attached["items"][0]["preview"]["after"]
    assert "Q-1" in after
    # Lesson content untouched by the preview branch.
    assert "Q-1" not in frappe.get_document("Course Lesson", "LS-1")["data"]["content"]


def _seed_quiz_assignment_exercise(frappe):
    frappe.docs.setdefault("LMS Quiz", []).append({
        "name": "QZ-1", "title": "Check 1", "course": "PY-101",
        "passing_percentage": 50, "modified": "v9", "questions": [],
    })
    frappe.docs.setdefault("LMS Question", [])
    frappe.docs.setdefault("LMS Assignment", []).append({
        "name": "AS-1", "title": "Write", "question": "Submit it",
        "type": "Text", "course": "PY-101", "modified": "v4",
    })
    frappe.docs.setdefault("LMS Programming Exercise", []).append({
        "name": "EX-1", "title": "Echo", "problem_statement": "Echo input",
        "language": "Python", "modified": "v7",
        "test_cases": [{"input": "a", "expected_output": "a"}],
    })


def _sample_questions():
    return [
        {"question": "Best prompt?", "type": "Choices", "marks": 2,
         "options": [{"text": "Specific", "correct": 1}, {"text": "Vague", "correct": 0}]},
        {"question": "Say hi", "type": "User Input", "marks": 1},
    ]


def test_manage_quiz_create_preview_defers_questions(tmp_path, monkeypatch):
    _isolate_db(monkeypatch, tmp_path)
    frappe = FakeFrappe()
    frappe.docs.setdefault("LMS Quiz", [])
    frappe.docs.setdefault("LMS Question", [])
    registry = _registry(frappe)
    result = registry.get("manage_quiz").func({
        "operation": "create", "title": "Check 1", "course": "PY-101",
        "questions": _sample_questions(), "dry_run": True,
        "member": "teacher@example.com",
    })
    _assert_no_writes(frappe)
    assert result["status"] == "pending_approval"
    assert result["plan_id"]
    assert result["requires_edit_review"] is True
    assert result["reversibility"] == "compensating"
    # Question creation is deferred: no LMS Question or LMS Quiz rows exist.
    assert frappe.docs["LMS Question"] == []
    assert frappe.docs["LMS Quiz"] == []
    questions = [item for item in result["items"] if item.get("kind") == "question"]
    assert len(questions) == 2
    first = questions[0]["preview"]
    assert first["before"] is None
    assert first["after"]["document"]["question"] == "Best prompt?"
    assert first["after"]["document"]["option_1"] == "Specific"
    assert first["after"]["document"]["is_correct_1"] == 1
    assert first["after"]["marks"] == 2
    assert any(
        item.get("kind") == "field" and item["preview"]["after"] == "Check 1"
        for item in result["items"]
    )
    stored = write_plans.get_plan(result["plan_id"], tmp_path / "plans.db")
    assert stored is not None
    assert stored["expected_modified"] == {}
    # Edited payload support: reviewer edits apply to the question preview.
    edited = write_plans.patch_item(
        result["plan_id"], questions[0]["id"],
        {"doctype": "LMS Question",
         "document": {**first["after"]["document"], "question": "Edited?"},
         "marks": 3},
        tmp_path / "plans.db",
    )
    assert edited is not None
    edited_item = next(item for item in edited["items"] if item["id"] == questions[0]["id"])
    assert edited_item["status"] == "edited"
    assert edited_item["preview"]["after"]["marks"] == 3
    assert edited_item["preview"]["after"]["document"]["question"] == "Edited?"


def test_manage_quiz_update_and_delete_preview(tmp_path, monkeypatch):
    _isolate_db(monkeypatch, tmp_path)
    frappe = FakeFrappe()
    _seed_quiz_assignment_exercise(frappe)
    registry = _registry(frappe)
    updated = registry.get("manage_quiz").func({
        "operation": "update", "quiz": "QZ-1", "passing_percentage": 80,
        "questions": _sample_questions()[:1], "dry_run": True,
        "member": "teacher@example.com",
    })
    deleted = registry.get("manage_quiz").func({
        "operation": "delete", "quiz": "QZ-1",
        "dry_run": True, "member": "teacher@example.com",
    })
    _assert_no_writes(frappe)
    assert updated["status"] == "pending_approval" == deleted["status"]
    assert updated["reversibility"] == "compensating"
    assert updated["requires_edit_review"] is True
    field_item = next(
        item for item in updated["items"]
        if item.get("kind") == "field"
        and item.get("payload", {}).get("fields", {}).get("passing_percentage") == 80
    )
    assert field_item["preview"] == {"before": 50, "after": 80}
    question_items = [item for item in updated["items"] if item.get("kind") == "question"]
    assert len(question_items) == 1
    assert question_items[0]["preview"]["before"] is None
    assert question_items[0]["preview"]["after"]["document"]["option_1"] == "Specific"
    stored_update = write_plans.get_plan(updated["plan_id"], tmp_path / "plans.db")
    assert stored_update["expected_modified"] == {"LMS Quiz:QZ-1": "v9"}
    assert deleted["typed_confirm"] == "QZ-1"
    stored_delete = write_plans.get_plan(deleted["plan_id"], tmp_path / "plans.db")
    assert stored_delete["expected_modified"] == {"LMS Quiz:QZ-1": "v9"}
    assert frappe.docs["LMS Question"] == []
    assert frappe.get_document("LMS Quiz", "QZ-1")["data"]["passing_percentage"] == 50


def test_manage_assignment_create_update_delete_preview(tmp_path, monkeypatch):
    _isolate_db(monkeypatch, tmp_path)
    frappe = FakeFrappe()
    _seed_quiz_assignment_exercise(frappe)
    registry = _registry(frappe)
    created = registry.get("manage_assignment").func({
        "operation": "create", "title": "New", "question": "Do it",
        "type": "Text", "dry_run": True, "member": "teacher@example.com",
    })
    updated = registry.get("manage_assignment").func({
        "operation": "update", "name": "AS-1", "title": "Write 2",
        "dry_run": True, "member": "teacher@example.com",
    })
    deleted = registry.get("manage_assignment").func({
        "operation": "delete", "name": "AS-1",
        "dry_run": True, "member": "teacher@example.com",
    })
    _assert_no_writes(frappe)
    assert created["status"] == updated["status"] == deleted["status"] == "pending_approval"
    assert created["plan_id"] and updated["plan_id"] and deleted["plan_id"]
    assert all(item["preview"]["before"] is None for item in created["items"])
    assert {item["preview"]["after"] for item in created["items"]} >= {"New", "Do it", "Text"}
    title_item = next(
        item for item in updated["items"]
        if item.get("payload", {}).get("fields", {}).get("title") == "Write 2"
    )
    assert title_item["preview"] == {"before": "Write", "after": "Write 2"}
    stored_update = write_plans.get_plan(updated["plan_id"], tmp_path / "plans.db")
    assert stored_update["expected_modified"] == {"LMS Assignment:AS-1": "v4"}
    assert deleted["typed_confirm"] == "AS-1"
    assert frappe.get_document("LMS Assignment", "AS-1")["data"]["title"] == "Write"


def test_manage_programming_exercise_test_cases_preview(tmp_path, monkeypatch):
    _isolate_db(monkeypatch, tmp_path)
    frappe = FakeFrappe()
    _seed_quiz_assignment_exercise(frappe)
    registry = _registry(frappe)
    created = registry.get("manage_programming_exercise").func({
        "operation": "create", "title": "Echo", "problem_statement": "Echo input",
        "language": "Python",
        "test_cases": [{"input": "a", "expected_output": "a"},
                       {"input": "b", "expected_output": "b"}],
        "dry_run": True, "member": "teacher@example.com",
    })
    _assert_no_writes(frappe)
    assert created["status"] == "pending_approval"
    assert created["requires_edit_review"] is True
    cases = [item for item in created["items"] if item.get("kind") == "test_case"]
    assert len(cases) == 2
    assert cases[0]["preview"] == {"before": None, "after": {"input": "a", "expected_output": "a"}}
    assert cases[1]["preview"] == {"before": None, "after": {"input": "b", "expected_output": "b"}}
    updated = registry.get("manage_programming_exercise").func({
        "operation": "update", "name": "EX-1",
        "test_cases": [{"input": "a", "expected_output": "A"},
                       {"input": "z", "expected_output": "z"}],
        "dry_run": True, "member": "teacher@example.com",
    })
    _assert_no_writes(frappe)
    assert updated["status"] == "pending_approval"
    updated_cases = [item for item in updated["items"] if item.get("kind") == "test_case"]
    assert updated_cases[0]["preview"] == {
        "before": {"input": "a", "expected_output": "a"},
        "after": {"input": "a", "expected_output": "A"},
    }
    assert updated_cases[1]["preview"]["before"] is None
    stored = write_plans.get_plan(updated["plan_id"], tmp_path / "plans.db")
    assert stored["expected_modified"] == {"LMS Programming Exercise:EX-1": "v7"}
    assert frappe.get_document("LMS Programming Exercise", "EX-1")["data"]["test_cases"] == [
        {"input": "a", "expected_output": "a"}
    ]
