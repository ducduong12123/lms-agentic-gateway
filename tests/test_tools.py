"""Unit test đơn, chạy: python -m pytest -q (hoặc python file trực tiếp)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.connector.frappe_client import FrappeClient
from gateway.runtime.policy import allowed_tools
from gateway.tools.catalog import build_registry

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
print("OK tools:", names)
