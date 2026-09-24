"""Unit test đơn, chạy: python -m pytest -q (hoặc python file trực tiếp)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.connector.frappe_client import FrappeClient
from gateway.runtime.policy import allowed_tools
from gateway.tools.catalog import build_registry
from gateway.runtime.tool_bundles import select_bundles

reg = build_registry(FrappeClient("http://localhost"))
names = reg.names()
assert "search_courses" in names
assert "get_course_outline" in names
assert "get_lesson_context" in names
assert "get_my_progress" in names
assert "draft_quiz" in names
assert "update_course_content_after_approval" in names
assert reg.get("update_course_content_after_approval").needs_approval is True
assert reg.get("search_courses").needs_approval is False
assert "search_courses" in allowed_tools("student")
assert "update_course_content_after_approval" not in allowed_tools("student")
# student-safe: tool lesson phải tồn tại và có mô tả lọc instructor
assert "instructor" in reg.get("get_lesson_context").description


def test_bundle_router_limits_teacher_tools_to_relevant_domains():
    registry = build_registry(FrappeClient("http://localhost"))
    bundles = select_bundles(
        "Phân tích học viên yếu rồi cải thiện course Python",
        "teacher",
        {"kind": "course", "course": "PY-101"},
    )
    schemas = registry.schemas(allowed_tools("teacher"), set(bundles))
    names = {item["function"]["name"] for item in schemas}
    assert {"course.read", "course.authoring", "analytics.learning"} <= set(bundles)
    assert "manage_lesson" in names
    assert "list_at_risk_students" in names
    assert "get_course_outline" in names
    assert "remember_user_fact" not in names
    assert all(registry.get(name).bundles for name in registry.names())
    assert select_bundles("Tên tôi là An, hãy ghi nhớ", "student") == ["memory.personal"]


def test_teacher_course_authoring_intent_survives_natural_vietnamese_word_order():
    bundles = select_bundles(
        "tạo cho tôi các bài học trong khóa học ứng dụng coding harness",
        "admin",
    )
    assert {"course.read", "course.authoring", "course.workspace"} <= set(bundles)


def test_active_authoring_project_keeps_write_tools_on_short_follow_up():
    bundles = select_bundles(
        "ok chấp nhận",
        "admin",
        workflow={"active_project": {"id": "cprj-1", "status": "planning"}},
    )
    assert {"course.read", "course.authoring", "course.workspace"} <= set(bundles)


def test_confirmation_keeps_write_tools_after_course_discovery():
    bundles = select_bundles(
        "ok chấp nhận",
        "admin",
        workflow={"active_course": "ai-ung-dung"},
    )
    assert {"course.read", "course.authoring", "course.workspace"} <= set(bundles)


def test_student_course_question_does_not_receive_authoring_bundle():
    bundles = select_bundles("tạo cho tôi bài học này dễ hiểu hơn", "student")
    assert "course.authoring" not in bundles
print("OK tools:", names)
