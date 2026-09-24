from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.runtime import agent_loop, course_projects, features, learner


def test_course_project_survives_sessions_and_preserves_unsent_fields(tmp_path):
    db = tmp_path / "projects.db"
    first = features.ensure_chat_session("teacher@test.com", "", "Khóa AI", path=db)
    project = course_projects.checkpoint_project(
        "teacher@test.com",
        first["id"],
        title="AI ứng dụng",
        status="planning",
        patch={
            "audience": "Giảng viên đại học",
            "learning_outcomes": ["Xây được ứng dụng LLM"],
            "module_plan": [
                {"id": "m1", "title": "LLM căn bản", "status": "planned"},
                {"id": "m2", "title": "Memory và RAG", "status": "planned"},
            ],
            "next_actions": ["Tạo LMS Course"],
        },
        path=db,
    )

    course_projects.checkpoint_project(
        "teacher@test.com",
        first["id"],
        project_id=project["id"],
        patch={"current_focus": "Module 1"},
        path=db,
    )
    restored = course_projects.active_project("teacher@test.com", first["id"], path=db)

    assert restored is not None
    assert restored["state"]["audience"] == "Giảng viên đại học"
    assert len(restored["state"]["module_plan"]) == 2
    assert restored["state"]["current_focus"] == "Module 1"

    second = features.ensure_chat_session("teacher@test.com", "", "Tiếp tục", path=db)
    reopened = course_projects.checkpoint_project(
        "teacher@test.com", second["id"], project_id=project["id"], path=db,
    )
    assert course_projects.active_project("teacher@test.com", second["id"], path=db) == reopened
    assert course_projects.list_projects("teacher@test.com", path=db)[0]["id"] == project["id"]

    another = course_projects.checkpoint_project(
        "teacher@test.com",
        second["id"],
        title="AI nâng cao",
        new_project=True,
        path=db,
    )
    assert another["id"] != project["id"]
    assert course_projects.active_project("teacher@test.com", second["id"], path=db) == another


def test_executed_authoring_action_advances_canonical_checkpoint(tmp_path):
    db = tmp_path / "projects.db"
    session = features.ensure_chat_session("teacher@test.com", "", "Khóa AI", path=db)
    project = course_projects.checkpoint_project(
        "teacher@test.com",
        session["id"],
        title="AI ứng dụng",
        patch={"next_actions": ["Tạo khóa học"]},
        path=db,
    )

    updated = course_projects.record_authoring_result(
        "teacher@test.com",
        session["id"],
        "manage_course",
        {"operation": "create", "title": "AI ứng dụng"},
        {
            "status": "done",
            "changes": [{"doctype": "LMS Course", "name": "ai-ung-dung", "op": "create"}],
        },
        path=db,
    )

    assert updated is not None
    assert updated["id"] == project["id"]
    assert updated["course"] == "ai-ung-dung"
    assert updated["status"] == "authoring"
    assert updated["state"]["completed_items"] == ["create LMS Course: ai-ung-dung"]


def test_authoring_state_links_discovered_lms_course_to_active_project(tmp_path, monkeypatch):
    db = tmp_path / "projects.db"
    monkeypatch.setattr(learner, "DB", db)
    session = features.ensure_chat_session("teacher@test.com", "", "Khóa AI", path=db)
    project = course_projects.checkpoint_project(
        "teacher@test.com",
        session["id"],
        title="AI ứng dụng",
        path=db,
    )
    context = {
        "user": "teacher@test.com",
        "session_id": session["id"],
        "workflow_state": features.workflow_context("teacher@test.com", session["id"]),
    }

    agent_loop._capture_tool_workflow(
        context,
        "get_course_authoring_state",
        {"course": "ai-ung-dung"},
        {"course": {"name": "ai-ung-dung"}, "chapters": []},
    )

    linked = course_projects.get_project("teacher@test.com", project["id"], path=db)
    assert linked["course"] == "ai-ung-dung"
    assert context["workflow_state"]["active_course"] == "ai-ung-dung"
