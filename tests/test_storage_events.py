from __future__ import annotations

from gateway.runtime import learner
from gateway.runtime.events import EventProcessor


def test_evidence_is_idempotent_and_mastery_recomputes(tmp_path):
    path = tmp_path / "gateway.db"
    concept = learner.upsert_concept(
        "Variables", course="COURSE-1", status="approved", path=path
    )["id"]
    payload = dict(
        member="learner@example.com",
        concept_id=concept,
        kind="quiz_item",
        outcome=1.0,
        ref_doctype="LMS Quiz Submission",
        ref_name="SUB-1",
        ref_modified="2026-09-15 10:00:00::QUESTION-1",
        path=path,
    )

    assert learner.record_evidence(**payload)["duplicate"] is False
    assert learner.record_evidence(**payload)["duplicate"] is True
    before = learner.mastery_snapshot("learner@example.com", path)[0]["p"]
    learner.recompute_mastery(path=path)
    assert learner.mastery_snapshot("learner@example.com", path)[0]["p"] == before


def test_event_inbox_deduplicates_webhook_delivery(tmp_path):
    processor = EventProcessor(_FakeFrappe(), path=tmp_path / "gateway.db")
    event = {
        "doctype": "Course Lesson",
        "name": "LESSON-1",
        "modified": "2026-09-15 10:00:00",
    }
    assert processor.enqueue(event)["queued"] is True
    assert processor.enqueue(event)["duplicate"] is True


class _FakeFrappe:
    def get_document(self, doctype, name):
        if doctype == "Course Lesson":
            return {"data": {"name": name, "course": "COURSE-1"}}
        assert doctype == "LMS Quiz Submission"
        return {
            "data": {
                "name": name,
                "modified": "2026-09-15 10:00:00",
                "member": "learner@example.com",
                "course": "COURSE-1",
                "quiz": "QUIZ-1",
                "result": [
                    {"question_name": "QUESTION-1", "is_correct": 1},
                    {"question_name": "QUESTION-2", "is_correct": 0},
                ],
            }
        }

    def list_documents(self, doctype, **kwargs):
        if doctype == "Course Lesson":
            return {"data": [{"name": "LESSON-1", "course": "COURSE-1"}]}
        return {"data": []}


def test_quiz_event_creates_per_question_evidence(tmp_path):
    path = tmp_path / "gateway.db"
    processor = EventProcessor(_FakeFrappe(), path=path)
    processor.enqueue(
        {
            "doctype": "LMS Quiz Submission",
            "name": "SUB-1",
            "modified": "2026-09-15 10:00:00",
        }
    )

    assert processor.process_pending() == 1
    with learner._conn(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 2
        row = conn.execute(
            "SELECT n_evidence FROM mastery WHERE member=?",
            ("learner@example.com",),
        ).fetchone()
        assert row["n_evidence"] == 2
    assert processor.stats()["pending_events"] == 0
