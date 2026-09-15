"""Policy: agent nào được dùng tool nào. LLM không được tự ý gọi tool khác."""
from __future__ import annotations

# 4 copilot theo thiết kế. Tool ghi nguy hiểm luôn needs_approval ở registry.
POLICY: dict[str, list[str]] = {
    "student": [
        "search_courses",
        "get_course_outline",
        "get_lesson_context",
        "get_my_progress",
        "get_my_mastery",
        "record_feedback_correction",
        "remember_user_fact",
        "recall_user_facts",
        "forget_user_fact",
    ],
    "teacher": [
        "search_courses",
        "get_course_outline",
        "get_lesson_context",
        "get_batch_progress",
        "find_at_risk_students",
        "draft_quiz",
        "draft_assignment_feedback",
        "remember_user_fact",
        "recall_user_facts",
        "forget_user_fact",
    ],
    "evaluator": [
        "get_assignment_submissions",
        "get_quiz_submissions",
        "draft_assignment_feedback",
        "get_my_mastery",
        "remember_user_fact",
        "recall_user_facts",
        "forget_user_fact",
    ],
    "admin": [
        "search_courses",
        "get_course_outline",
        "get_lesson_context",
        "get_batch_progress",
        "find_at_risk_students",
        "update_course_content_after_approval",
        "remember_user_fact",
        "recall_user_facts",
        "forget_user_fact",
    ],
}


def allowed_tools(role: str) -> list[str]:
    return POLICY.get(role, POLICY["student"])
