import json
from datetime import datetime

from gateway.runtime import features, learner
from gateway.runtime.scheduler import ProactiveWorker


class FakeLLM:
    def __init__(self, content):
        self.content = content

    def chat(self, messages, tools=None):
        return {"choices": [{"message": {"content": json.dumps(self.content)}}]}


class FakeFrappe:
    def __init__(self):
        self.created = []

    def get_document(self, doctype, name):
        if doctype == "Course Lesson":
            return {"data": {"name": name, "title": "Vòng lặp", "course": "PY-101", "body": "for và while", "quiz_id": "QUIZ-1"}}
        return {"data": {"questions": [{"question": "Q-1", "question_detail": "for dùng khi nào?"}]}}

    def create_document(self, doctype, doc):
        self.created.append((doctype, doc))
        return {"data": {"name": "LOG-1"}}


def test_extract_approve_and_only_then_drive_mastery(tmp_path):
    path = tmp_path / "gateway.db"
    llm = FakeLLM([{
        "label": "Vòng lặp for", "description": "Lặp hữu hạn",
        "prerequisites": [], "questions": ["Q-1"],
    }])
    concepts = features.extract_concepts(llm, FakeFrappe(), "LESSON-1", path)
    concept_id = concepts[0]["id"]
    assert learner.concepts_for_lesson("LESSON-1", path) == []
    assert learner.approve_concept(concept_id, path)
    assert learner.concepts_for_lesson("LESSON-1", path)[0]["id"] == concept_id
    with learner._conn(path) as conn:
        assert conn.execute("SELECT concept_id FROM question_concept WHERE question='Q-1'").fetchone()[0] == concept_id


def test_feynman_rubric_becomes_external_evidence(tmp_path):
    path = tmp_path / "gateway.db"
    concept = learner.upsert_concept("Loops", status="approved", path=path)["id"]
    llm = FakeLLM({
        "correctness": 1, "completeness": .8, "example": .6, "limits": .4,
        "feedback": "Bổ sung giới hạn.",
    })
    result = features.evaluate_session(
        llm, "student@test.com", "feynman", concept,
        "Tôi giải thích vòng lặp với một ví dụ đủ dài.", path,
    )
    assert result["score"] == .7
    assert learner.mastery_snapshot("student@test.com", path)[0]["n_evidence"] == 1


def test_daily_plan_is_opt_in_and_notified_once(tmp_path, monkeypatch):
    monkeypatch.setattr(learner, "DB", tmp_path / "gateway.db")
    learner.set_member_pref("student@test.com", True)
    frappe = FakeFrappe()
    worker = ProactiveWorker(frappe)
    now = datetime(2026, 9, 15, 7, 0, tzinfo=worker.timezone)
    assert worker.run_daily(now)["sent"] == 1
    assert worker.run_daily(now)["sent"] == 0
    assert len(frappe.created) == 1


def test_teacher_action_is_single_use_and_audited(tmp_path):
    path = tmp_path / "gateway.db"
    action = features.propose_teacher_action(
        "PY-101", "student@test.com", "Cần hỗ trợ", "Bạn cần hỗ trợ gì?", "teacher@test.com", path
    )
    frappe = FakeFrappe()
    result = features.approve_teacher_action(action["id"], "teacher@test.com", frappe, path)
    assert result["status"] == "executed"
    try:
        features.approve_teacher_action(action["id"], "teacher@test.com", frappe, path)
        assert False, "action must be one-use"
    except ValueError:
        pass


def test_preferences_default_off_and_member_can_be_erased(tmp_path):
    path = tmp_path / "gateway.db"
    assert learner.get_member_pref("student@test.com", path)["daily_plan_opt_in"] is False
    learner.set_member_pref("student@test.com", True, path=path)
    assert features.erase_member("student@test.com", path)["deleted_rows"] == 1


def test_qa_escalation_is_deduplicated_and_teacher_can_list_it(tmp_path):
    path = tmp_path / "gateway.db"
    first = features.create_qa_escalation("student@test.com", "chat-1", "PY-101", "LESSON-1", "Tại sao code này lỗi?", path=path)
    second = features.create_qa_escalation("student@test.com", "chat-1", "PY-101", "LESSON-1", "Tại sao code này lỗi?", path=path)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["id"] == first["id"]
    rows = features.list_qa_escalations("PY-101", path=path)
    assert [row["id"] for row in rows] == [first["id"]]
    assert rows[0]["status"] == "open"
    erased = features.erase_member("student@test.com", path)
    assert erased["deleted_rows"] >= 1
    assert features.list_qa_escalations("PY-101", path=path) == []
