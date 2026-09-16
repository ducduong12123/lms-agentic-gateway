from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.runtime import runs


def test_agent_run_pauses_and_resumes_after_approval(tmp_path):
    database = tmp_path / "runs.db"
    plan = {
        "goal": "Cập nhật khóa học",
        "bundles": ["course.read", "course.authoring"],
        "steps": [{"id": "execute", "label": "Cập nhật nội dung", "status": "pending"}],
    }

    run = runs.start_or_resume("teacher@example.com", "session-1", plan, database)
    step_id = runs.start_step(
        str(run["id"]),
        "Cập nhật bài học",
        "manage_lesson",
        {"operation": "update", "lesson": "LESSON-1"},
        database,
    )
    runs.finish_step(
        step_id,
        {"status": "pending_approval"},
        "waiting_approval",
        "approval-1",
        database,
    )

    waiting = runs.active_run("teacher@example.com", "session-1", database)
    assert waiting and waiting["status"] == "waiting_approval"
    resumed = runs.complete_approval("approval-1", {"ok": True}, database)
    assert resumed and resumed["status"] == "running"
    assert resumed["tool_steps"][0]["status"] == "completed"

    same = runs.start_or_resume("teacher@example.com", "session-1", plan, database)
    assert same["id"] == run["id"]
    runs.complete_run(str(run["id"]), path=database)
    assert runs.active_run("teacher@example.com", "session-1", database) is None
