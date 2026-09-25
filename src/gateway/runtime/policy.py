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
        "enroll_course",
        "mark_lesson_complete",
        "save_note",
        "create_review_set",
        "schedule_review",
        "set_goal",
        "start_session",
        "navigate",
        "render_view",
    ],
    "teacher": [
        "search_courses",
        "get_course_outline",
        "get_lesson_context",
        "get_batch_progress",
        "list_at_risk_students",
        "get_student_mastery",
        "draft_quiz",
        "draft_assignment_feedback",
        "remember_user_fact",
        "recall_user_facts",
        "forget_user_fact",
        "message_students",
        "create_live_class",
        "publish_lesson_draft",
        "analyze_course_gaps",
        "get_course_authoring_state",
        "get_course_categories",
        "list_course_projects",
        "get_course_project",
        "update_course_project",
        "manage_course",
        "manage_chapter",
        "manage_lesson",
        "manage_quiz",
        "manage_lesson_block",
        "manage_assignment",
        "manage_programming_exercise",
        "navigate",
        "render_view",
    ],
    "evaluator": [
        "get_assignment_submissions",
        "get_quiz_submissions",
        "draft_assignment_feedback",
        "get_my_mastery",
        "remember_user_fact",
        "recall_user_facts",
        "forget_user_fact",
        "navigate",
        "render_view",
    ],
    "admin": [
        "search_courses",
        "get_course_outline",
        "get_lesson_context",
        "get_batch_progress",
        "list_at_risk_students",
        "get_student_mastery",
        "update_course_content_after_approval",
        "message_students",
        "create_live_class",
        "publish_lesson_draft",
        "analyze_course_gaps",
        "get_course_authoring_state",
        "get_course_categories",
        "list_course_projects",
        "get_course_project",
        "update_course_project",
        "manage_course",
        "manage_chapter",
        "manage_lesson",
        "manage_quiz",
        "manage_assignment",
        "manage_lesson_block",
        "manage_programming_exercise",
        "remember_user_fact",
        "recall_user_facts",
        "forget_user_fact",
        "navigate",
        "render_view",
    ],
}


# Tool lms_copilot được đăng ký động theo catalog mà Frappe trả cho chính user này,
# nên mọi tool copilot_* có trong registry đều đã qua lọc role ở phía Frappe.
COPILOT_PREFIX = "copilot_"
# Tool ghi nhật ký/báo cáo do runtime gọi theo luật cố định (tutor.finish_turn, weekly_insight);
# LLM không được tự gọi, để số liệu và trích dẫn luôn đi qua bước kiểm tra của Gateway.
RUNTIME_ONLY = {"copilot_log_conversation_turn", "copilot_save_weekly_insight"}


def allowed_tools(role: str, registry=None) -> list[str]:
    allowed = list(POLICY.get(role, POLICY["student"]))
    if registry is not None:
        allowed += [
            name for name in registry.names()
            if name.startswith(COPILOT_PREFIX) and name not in RUNTIME_ONLY
        ]
    return allowed
