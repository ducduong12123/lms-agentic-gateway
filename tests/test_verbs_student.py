import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.runtime.tool_registry import ToolRegistry
from gateway.tools import verbs_student


class FakeFrappe:
    def __init__(self):
        self.docs = {"LMS Enrollment": [], "LMS Course Progress": [], "LMS Lesson Note": []}
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

    def update_document(self, doctype, name, doc):
        for row in self.docs.get(doctype, []):
            if row["name"] == name:
                row.update(doc)
                return {"data": row}
        raise ValueError("missing document")


def test_student_mutations_are_idempotent_and_return_undo(monkeypatch):
    frappe = FakeFrappe()
    registry = ToolRegistry()
    verbs_student.register(registry, frappe)

    args = {"member": "student@example.com", "course": "PY-101"}
    first = registry.get("enroll_course").func(dict(args))
    second = registry.get("enroll_course").func(dict(args))
    assert first["status"] == "done" and first["undo"]["available"]
    assert second["status"] == "noop"

    progress = registry.get("mark_lesson_complete").func({"member": args["member"], "course": "PY-101", "chapter": 1, "lesson": "2"})
    note = registry.get("save_note").func({"member": args["member"], "course": "PY-101", "lesson": "LESSON-2", "note": "closure nhớ scope"})
    assert progress["status"] == "done" and note["status"] == "done"

    monkeypatch.setattr(verbs_student.learner, "weak_concepts", lambda *a, **k: [{"id": "c1", "label": "Closure", "description": "scope"}])
    monkeypatch.setattr(verbs_student.features, "create_review_set", lambda *a, **k: {"id": "rs_1"})
    review = registry.get("create_review_set").func({"member": args["member"], "course": "PY-101", "size": 1})
    assert review["view"] == {"type": "review_set", "set_id": "rs_1"}

    monkeypatch.setattr(verbs_student.features, "scheduled_reviews_for", lambda member: [])
    monkeypatch.setattr(verbs_student.features, "schedule_review", lambda *a, **k: {"id": "rev_1", "concept_id": "c1", "due_date": "2026-01-01"})
    scheduled = registry.get("schedule_review").func({"member": args["member"], "concept_id": "c1", "when": "2026-01-01"})
    assert scheduled["status"] == "done"

    monkeypatch.setattr(verbs_student.learner, "get_member_pref", lambda member: {"member": member, "daily_plan_opt_in": False, "quiet_hours": ""})
    monkeypatch.setattr(verbs_student.learner, "set_member_pref", lambda *a, **k: {"member": a[0], "daily_plan_opt_in": True, "quiet_hours": "22:00-07:00"})
    goal = registry.get("set_goal").func({"member": args["member"], "daily_plan_opt_in": True, "quiet_hours": "22:00-07:00"})
    session = registry.get("start_session").func({"member": args["member"], "mode": "check", "concept_id": "c1"})
    assert goal["status"] == "done" and session["view"]["type"] == "session"


def test_review_set_uses_llm_questions_when_available(monkeypatch):
    class FakeLLM:
        def chat(self, messages):
            return {"choices": [{"message": {"content": '[{"concept_id":"c1","prompt":"Vì sao closure giữ được scope?","expected":"Closure","explanation":"Scope được đóng lại."}]'}}]}

    monkeypatch.setattr(verbs_student.learner, "weak_concepts", lambda *a, **k: [{"id": "c1", "label": "Closure", "description": "scope"}])
    monkeypatch.setattr(verbs_student.features, "find_open_review_set", lambda *a, **k: None)
    monkeypatch.setattr(verbs_student.features, "create_review_set", lambda *a, **k: {"id": "rs_llm"})
    registry = ToolRegistry()
    verbs_student.register(registry, FakeFrappe(), FakeLLM())

    result = registry.get("create_review_set").func({"member": "student@example.com", "course": "PY-101", "size": 1})

    assert result["view"] == {"type": "review_set", "set_id": "rs_llm"}
