import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.runtime import plan_apply, write_plans
from gateway.tools import plan_executors


class FakeFrappe:
    def __init__(self):
        self.docs = {
            "LMS Course": [{"name": "PY-101", "title": "Python", "modified": "v1"}],
            "LMS Quiz": [{"name": "QZ-1", "title": "Check", "passing_percentage": 50, "modified": "q1"}],
            "LMS Question": [],
        }
        self.writes = []

    def get_document(self, doctype, name):
        row = next((item for item in self.docs.get(doctype, []) if item.get("name") == name), {})
        return {"data": dict(row)}

    def update_document(self, doctype, name, fields):
        self.writes.append((doctype, name, dict(fields)))
        for row in self.docs.get(doctype, []):
            if row.get("name") == name:
                row.update(fields)
                return {"data": dict(row)}
        raise ValueError("missing document")

    def create_document(self, doctype, fields):
        self.writes.append((doctype, "(new)", dict(fields)))
        row = {"name": f"{doctype}-1", **dict(fields)}
        self.docs.setdefault(doctype, []).append(row)
        return {"data": dict(row)}

    def delete_document(self, doctype, name):
        self.writes.append((doctype, name, {"op": "delete"}))
        return {"data": {"name": name}}


def _plan(**overrides):
    base = {
        "id": "plan_1",
        "tool": "manage_course",
        "status": "pending",
        "args": {"operation": "update", "course": "PY-101"},
        "items": [
            {"id": "title", "label": "Title", "status": "pending", "payload": {"fields": {"title": "New"}}},
            {"id": "description", "label": "Description", "status": "pending", "payload": {"fields": {"description": "New"}}},
        ],
        "expected_modified": {"LMS Course:PY-101": "v1"},
        "reversibility": "reversible",
    }
    base.update(overrides)
    return base


def test_item_selection_applies_only_approved_fields():
    frappe = FakeFrappe()
    plan = _plan()
    selected, merged = plan_apply.apply_plan_selection(
        plan, [{"id": "title", "status": "approved"}, {"id": "description", "status": "rejected"}],
    )

    result = plan_executors.apply_manage_course_update(frappe, plan, merged)

    assert [item["id"] for item in selected] == ["title"]
    assert frappe.docs["LMS Course"][0]["title"] == "New"
    assert frappe.docs["LMS Course"][0].get("description") is None
    assert result["status"] == "done"
    assert result["undo"]["fields"] == {"title": "Python"}


def test_expected_modified_mismatch_blocks_write():
    frappe = FakeFrappe()
    frappe.docs["LMS Course"][0]["modified"] = "v2"
    plan = _plan()
    selected, merged = plan_apply.apply_plan_selection(plan, None)

    try:
        plan_executors.apply_manage_course_update(frappe, plan, merged)
    except ValueError as exc:
        assert "đã thay đổi" in str(exc)
    else:
        raise AssertionError("stale plan should not apply")
    assert frappe.writes == []


def test_quiz_update_applies_selected_questions_with_compensating_undo():
    frappe = FakeFrappe()
    plan = {
        "id": "plan_quiz",
        "tool": "manage_quiz",
        "status": "pending",
        "args": {"operation": "update", "quiz": "QZ-1"},
        "items": [
            {"id": "field", "status": "pending", "payload": {"fields": {"passing_percentage": 80}}},
            {"id": "q1", "status": "pending", "payload": {"document": {"question": "New?", "type": "Choices"}, "marks": 2}},
            {"id": "q2", "status": "pending", "payload": {"document": {"question": "Skipped?", "type": "Choices"}, "marks": 1}},
        ],
        "expected_modified": {"LMS Quiz:QZ-1": "q1"},
        "reversibility": "compensating",
    }
    selected, merged = plan_apply.apply_plan_selection(
        plan, [{"id": "field"}, {"id": "q1"}],
    )

    result = plan_executors.apply_manage_quiz_update(frappe, plan, merged)

    assert [item["id"] for item in selected] == ["field", "q1"]
    assert frappe.docs["LMS Quiz"][0]["passing_percentage"] == 80
    assert len(frappe.docs["LMS Question"]) == 1
    assert result["undo"]["op"] == "restore_many"


def test_write_plan_item_edit_persists_before_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(write_plans, "DB", tmp_path / "plans.db")
    plan = write_plans.create_plan(
        "manage_course", "teacher@example.com", {"operation": "update", "course": "PY-101"},
        [{"id": "title", "label": "Title", "kind": "field", "preview": {"before": "Old", "after": "New"}, "payload": {"fields": {"title": "New"}}}],
        [{"doctype": "LMS Course", "name": "PY-101", "op": "update", "field": "title"}],
        "reversible", {"LMS Course:PY-101": "v1"},
    )

    updated = write_plans.patch_item(str(plan["id"]), "title", {"fields": {"title": "Edited"}})

    assert updated is not None
    edited = next(item for item in updated["items"] if item["id"] == "title")
    assert edited["status"] == "edited"
    assert edited["payload"] == {"fields": {"title": "Edited"}}


def test_quiz_update_undo_removes_created_questions():
    frappe = FakeFrappe()
    before = dict(frappe.get_document("LMS Quiz", "QZ-1")["data"])
    plan = {
        "id": "plan_quiz_undo",
        "tool": "manage_quiz",
        "status": "pending",
        "args": {"operation": "update", "quiz": "QZ-1"},
        "items": [
            {"id": "q1", "status": "pending", "payload": {"document": {"question": "New?", "type": "Choices"}, "marks": 1}},
        ],
        "expected_modified": {"LMS Quiz:QZ-1": "q1"},
        "reversibility": "compensating",
    }
    selected, merged = plan_apply.apply_plan_selection(plan, None)
    result = plan_executors.apply_manage_quiz_update(frappe, plan, merged)

    assert [item["id"] for item in selected] == ["q1"]
    assert len(frappe.docs["LMS Question"]) == 1
    assert result["undo"]["op"] == "restore_many"
    assert before["passing_percentage"] == 50
