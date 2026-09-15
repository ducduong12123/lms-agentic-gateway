from __future__ import annotations

from pathlib import Path

from gateway.api.webhook import should_ingest
from gateway.runtime import ingest, learner
from gateway.runtime.agent_loop import _initial_messages, learner_context_for_prompt


def _seed(tmp_path) -> tuple[Path, str]:
    db = tmp_path / "its.db"
    concept = learner.upsert_concept(
        "Vòng lặp Python", course="PY-101", description="for while",
        source_lesson="LESSON-9", path=db,
    )
    learner.approve_concept(concept["id"], path=db)
    learner.link_lesson_concept("LESSON-9", concept["id"], 1.0, path=db)
    return db, concept["id"]


def test_evidence_append_only_and_mastery_recompute_stable(tmp_path):
    db, concept_id = _seed(tmp_path)
    first = learner.record_evidence(
        "hv@test.com", concept_id, "quiz_item", 0.0,
        ref_doctype="LMS Quiz Submission", ref_name="Q9",
        ref_modified="v3::Q-A", path=db,
    )
    duplicate = learner.record_evidence(
        "hv@test.com", concept_id, "quiz_item", 1.0,
        ref_doctype="LMS Quiz Submission", ref_name="Q9",
        ref_modified="v3::Q-A", path=db,
    )

    assert duplicate["duplicate"] is True
    assert duplicate["id"] == first["id"]

    before = learner.mastery_snapshot("hv@test.com", path=db)
    assert before[0]["n_evidence"] == 1
    assert before[0]["p"] < learner.INITIAL_P

    recomputed = learner.recompute_mastery("hv@test.com", path=db)
    after = learner.mastery_snapshot("hv@test.com", path=db)
    assert before == after
    assert list(recomputed.values())[0] == after[0]["p"]


def test_only_approved_concepts_drive_tutoring(tmp_path):
    db, approved_id = _seed(tmp_path)
    draft = learner.upsert_concept(
        "Nháp chưa duyệt", course="PY-101", path=db,
    )
    learner.record_evidence(
        "hv@test.com", approved_id, "quiz_item", 0.0,
        ref_doctype="LMS Quiz Submission", ref_name="Q9",
        ref_modified="v3::Q-APPROVED", path=db,
    )
    learner.record_evidence(
        "hv@test.com", draft["id"], "quiz_item", 0.0,
        ref_doctype="LMS Quiz Submission", ref_name="Q9",
        ref_modified="v3::Q-B", path=db,
    )
    weak = learner.weak_concepts("hv@test.com", "PY-101", path=db)
    assert [item["concept_id"] for item in weak] == [approved_id]

def test_learner_isolation_per_member(tmp_path):
    db, concept_id = _seed(tmp_path)
    learner.record_evidence(
        "hv1@test.com", concept_id, "quiz_item", 0.0,
        ref_doctype="LMS Quiz Submission", ref_name="Q1",
        ref_modified="v1::Q-A", path=db,
    )
    assert learner.weak_concepts("hv2@test.com", "PY-101", path=db) == []
    assert learner.weak_concepts("Guest", "PY-101", path=db) == []


def test_f1_prompt_binds_weak_concept_and_lesson(tmp_path):
    db, concept_id = _seed(tmp_path)
    learner.record_evidence(
        "hv@test.com", concept_id, "quiz_item", 0.0,
        ref_doctype="LMS Quiz Submission", ref_name="Q9",
        ref_modified="v3::Q-A", path=db,
    )
    weak = learner.weak_concepts("hv@test.com", "PY-101", path=db)
    lesson = learner.concepts_for_lesson("LESSON-9", path=db)
    messages = _initial_messages(
        "rules", "bài này khó quá",
        {"recent_messages": [], "learner_states": weak, "lesson_concepts": lesson},
    )
    assert messages[0]["content"] == "rules"
    assert "Vòng lặp Python" in messages[1]["content"]
    assert messages[-1]["content"] == "bài này khó quá"

    empty = learner_context_for_prompt("Guest", "PY-101", "LESSON-9")
    assert empty == {"learner_states": [], "lesson_concepts": []}


def test_quiz_ingest_is_idempotent_per_question():
    class FakeFrappe:
        def get_document(self, doctype, name):
            assert doctype == "LMS Quiz Submission"
            return {"data": {
                "member": "hv@test.com",
                "modified": "v9",
                "result": [
                    {"question": "Q-A", "is_correct": 0},
                    {"question": "Q-B", "is_correct": 1},
                ],
            }}

    class FakePath:
        pass

    import tempfile

    db = Path(tempfile.mkdtemp()) / "ingest.db"
    concept_a = learner.upsert_concept("A", course="PY-101", path=db)
    concept_b = learner.upsert_concept("B", course="PY-101", path=db)
    learner.approve_concept(concept_a["id"], path=db)
    learner.approve_concept(concept_b["id"], path=db)
    learner.link_question_concept("Q-A", concept_a["id"], path=db)
    learner.link_question_concept("Q-B", concept_b["id"], path=db)

    first = ingest.ingest_quiz_submission(FakeFrappe(), "SUB-1", path=db)
    second = ingest.ingest_quiz_submission(FakeFrappe(), "SUB-1", path=db)
    assert first["evidence"] == 2
    assert second["duplicate"] is True
    assert learner.mastery_snapshot("hv@test.com", path=db)


def test_webhook_marks_its_events_for_ingest():
    assert should_ingest({"doctype": "LMS Quiz Submission", "name": "Q1"})
    assert should_ingest({"doctype": "LMS Assignment Submission", "name": "A1"})
    assert should_ingest({"doctype": "LMS Course Progress", "name": "P1"})
    assert should_ingest({"doctype": "Course Lesson", "name": "L1"})
    assert not should_ingest({"doctype": "LMS Quiz Submission", "name": ""})
