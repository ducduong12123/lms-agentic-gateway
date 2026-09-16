"""Domain tool bundles, capability routing, and concise request plans."""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from .tool_registry import ToolRegistry


@dataclass(frozen=True)
class ToolBundle:
    name: str
    label: str
    description: str
    tools: frozenset[str]


BUNDLES: dict[str, ToolBundle] = {
    "course.read": ToolBundle(
        "course.read", "Đọc khóa học", "Tìm và đọc cấu trúc, bài học, trạng thái biên soạn.",
        frozenset({"search_courses", "get_course_outline", "get_lesson_context", "get_course_authoring_state"}),
    ),
    "course.authoring": ToolBundle(
        "course.authoring", "Biên soạn khóa học", "Tạo và cập nhật nội dung khóa học.",
        frozenset({
            "draft_quiz", "draft_assignment_feedback", "update_course_content_after_approval",
            "publish_lesson_draft", "manage_course", "manage_chapter", "manage_lesson",
            "manage_lesson_block", "manage_quiz", "manage_assignment", "manage_programming_exercise",
        }),
    ),
    "memory.personal": ToolBundle(
        "memory.personal", "Ký ức dài hạn", "Ghi nhớ, tìm lại hoặc quên thông tin cá nhân.",
        frozenset({"remember_user_fact", "recall_user_facts", "forget_user_fact"}),
    ),
    "analytics.learning": ToolBundle(
        "analytics.learning", "Phân tích học tập", "Đọc tiến độ, mastery, bài nộp và rủi ro học tập.",
        frozenset({
            "get_my_progress", "get_batch_progress", "list_at_risk_students", "get_student_mastery",
            "get_my_mastery", "record_feedback_correction", "get_quiz_submissions",
            "get_assignment_submissions", "analyze_course_gaps",
        }),
    ),
    "student.learning": ToolBundle(
        "student.learning", "Hoạt động học tập", "Thao tác học tập được giới hạn cho chính học viên.",
        frozenset({
            "enroll_course", "mark_lesson_complete", "save_note", "create_review_set",
            "schedule_review", "set_goal", "start_session",
        }),
    ),
    "class.operations": ToolBundle(
        "class.operations", "Vận hành lớp học", "Liên lạc học viên và tổ chức lớp trực tiếp.",
        frozenset({"message_students", "create_live_class"}),
    ),
    "client.ui": ToolBundle(
        "client.ui", "Điều khiển giao diện", "Điều hướng LMS và hiển thị view đã kiểm soát.",
        frozenset({"navigate", "render_view"}),
    ),
}

_MUTATING = {
    "update_course_content_after_approval", "publish_lesson_draft", "manage_course", "manage_chapter",
    "manage_lesson", "manage_lesson_block", "manage_quiz", "manage_assignment",
    "manage_programming_exercise", "remember_user_fact", "forget_user_fact", "record_feedback_correction",
    "enroll_course", "mark_lesson_complete", "save_note", "create_review_set", "schedule_review", "set_goal",
    "message_students", "create_live_class",
}


def configure_registry(registry: ToolRegistry) -> None:
    """Attach bundle/risk metadata and reject drift at startup."""
    assigned: set[str] = set()
    for bundle in BUNDLES.values():
        for name in bundle.tools:
            registry.assign_bundle(name, bundle.name)
            assigned.add(name)
    missing = set(registry.names()) - assigned
    if missing:
        raise RuntimeError(f"tools without bundle: {sorted(missing)}")
    for name in _MUTATING:
        tool = registry.get(name)
        if tool is not None:
            tool.risk = "write"


def _plain(value: str) -> str:
    normalized = unicodedata.normalize("NFD", str(value or ""))
    return " ".join(normalized.encode("ascii", "ignore").decode().casefold().split())


def _has(text: str, *phrases: str) -> bool:
    return any(phrase in text for phrase in phrases)


def select_bundles(
    message: str,
    role: str,
    route: dict[str, object] | None = None,
    workflow: dict[str, object] | None = None,
) -> list[str]:
    """Select a small deterministic capability set; execution policy remains authoritative."""
    text = _plain(message)
    route = route or {}
    workflow = workflow or {}
    selected: set[str] = set()

    if route.get("kind") in {"course", "lesson"} or workflow.get("active_course"):
        selected.add("course.read")
    if _has(text, "khoa hoc", "course", "chuong", "chapter", "bai hoc", "lesson", "noi dung"):
        selected.add("course.read")
    if _has(text, "tao khoa", "sua khoa", "cap nhat khoa", "cai thien", "bien soan", "soan",
            "xuat ban", "luu vao lms", "tao bai", "sua bai", "tao quiz", "assignment",
            "bai lap trinh"):
        selected.update({"course.read", "course.authoring"})
    if _has(text, "nho", "ghi nho", "quen", "ten toi", "toi ten", "goi toi", "truoc day",
            "lan truoc", "ban nho", "muc tieu cua toi", "so thich", "trinh do cua toi"):
        selected.add("memory.personal")
    if _has(text, "thong ke", "so lieu", "tien do", "progress", "mastery", "hoc vien yeu", "nguy co",
            "bai nop", "submission", "diem", "phan tich", "gap"):
        selected.add("analytics.learning")
    if _has(text, "ghi danh", "hoan thanh bai", "luu ghi chu", "on tap", "lich on", "muc tieu hoc",
            "dat muc tieu", "feynman", "viva", "kiem tra"):
        selected.update({"student.learning", "analytics.learning"})
    if _has(text, "nhan hoc vien", "gui thong bao", "lop truc tiep", "live class"):
        selected.update({"analytics.learning", "class.operations"})
    if _has(text, "mo trang", "dieu huong", "hien thi", "xem tren lms"):
        selected.add("client.ui")

    if role in {"teacher", "admin"} and "course.authoring" in selected:
        selected.add("analytics.learning")
    if not selected:
        selected.add("course.read" if route.get("kind") in {"course", "lesson"} else "client.ui")
    return [name for name in BUNDLES if name in selected]


def plan_request(
    message: str,
    role: str,
    route: dict[str, object] | None = None,
    workflow: dict[str, object] | None = None,
) -> dict[str, object]:
    bundles = select_bundles(message, role, route, workflow)
    steps: list[dict[str, str]] = []
    if "course.read" in bundles:
        steps.append({"id": "discover", "label": "Xác định khóa học và ngữ cảnh", "status": "pending"})
    if "analytics.learning" in bundles:
        steps.append({"id": "analyze", "label": "Phân tích dữ liệu học tập liên quan", "status": "pending"})
    if any(name in bundles for name in ("memory.personal", "student.learning", "client.ui")):
        steps.append({"id": "context", "label": "Thu thập ngữ cảnh cần thiết", "status": "pending"})
    if any(name in bundles for name in ("course.authoring", "class.operations", "student.learning")):
        steps.append({"id": "execute", "label": "Thực hiện các thao tác phù hợp", "status": "pending"})
    steps.append({"id": "verify", "label": "Kiểm tra kết quả và tổng hợp", "status": "pending"})
    return {
        "goal": str(message or "").strip()[:500],
        "bundles": bundles,
        "bundle_labels": [BUNDLES[name].label for name in bundles],
        "steps": steps[:6],
    }


def summary_for_tools(tool_names: list[str]) -> str:
    if not tool_names:
        return "Đang xác định phạm vi yêu cầu và chọn nhóm công cụ phù hợp."
    used = [
        bundle.label.casefold()
        for bundle in BUNDLES.values()
        if bundle.tools.intersection(tool_names)
    ]
    unique = list(dict.fromkeys(used))
    if unique:
        return "Đã dùng dữ liệu từ " + ", ".join(unique) + " để kiểm tra ngữ cảnh và chuẩn bị kết quả."
    return "Đã kiểm tra dữ liệu cần thiết và tổng hợp kết quả từ các công cụ phù hợp."
