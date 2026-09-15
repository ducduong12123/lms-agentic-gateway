"""Business tools duy nhất LLM được thấy. Map đúng DocType thật đã kiểm."""
from __future__ import annotations

from ..connector.frappe_client import FrappeClient
from ..runtime.tool_registry import Tool, ToolRegistry

STUDENT_SAFE_LESSON_FIELDS = ["name", "title", "body", "content", "quiz_id", "course", "chapter", "include_in_preview"]


def _mock() -> bool:
    # Chưa cấu hình key thì trả mock để demo runtime/widget chạy được
    import os

    return not os.environ.get("FRAPPE_API_KEY")


def build_registry(frappe: FrappeClient) -> ToolRegistry:
    reg = ToolRegistry()

    # ---------- 1. search_courses ----------
    def search_courses(a: dict):
        q = a.get("query", "")
        if _mock():
            return {"data": [{"name": "PY-101", "title": "Python cơ bản", "source": "LMS Course: Python cơ bản"}]}
        res = frappe.list_documents("LMS Course", filters=[["title", "like", f"%{q}%"]], fields=["name", "title", "short_introduction"], limit=int(a.get("limit", 5)))
        return res

    reg.register(Tool("search_courses", "Tìm khóa học theo tên. Chỉ đọc.", {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 5}}, "required": ["query"]}, search_courses))

    # ---------- 2. get_course_outline ----------
    def get_course_outline(a: dict):
        course = a["course"]
        if _mock():
            return {"course": course, "chapters": [{"title": "Ch1", "lessons": ["Bài 1", "Bài 2"]}], "source": f"LMS Course: {course}"}
        doc = frappe.get_document("LMS Course", course).get("data", {})
        out = {"course": doc.get("title"), "chapters": []}
        for ch_ref in doc.get("chapters", []):
            ch = frappe.get_document("Course Chapter", ch_ref.get("chapter")).get("data", {})
            lessons = []
            for lr in ch.get("lessons", []):
                lessons.append(lr.get("lesson"))
            out["chapters"].append({"title": ch.get("title"), "lessons": lessons})
        out["source"] = f"LMS Course: {doc.get('title')}"
        return out

    reg.register(Tool("get_course_outline", "Lấy outline Course -> Chapter -> Lesson. Chỉ đọc.", {"type": "object", "properties": {"course": {"type": "string"}}, "required": ["course"]}, get_course_outline))

    # ---------- 3. get_lesson_context (student-safe) ----------
    def get_lesson_context(a: dict):
        lesson = a["lesson"]
        role = a.get("_role", "student")
        if _mock():
            return {"lesson": lesson, "body": "Nội dung demo...", "source": f"Course Lesson: {lesson}"}
        doc = frappe.get_document("Course Lesson", lesson).get("data", {})
        # Student không bao giờ thấy instructor_content / instructor_notes
        if role == "student":
            doc = {k: v for k, v in doc.items() if k in STUDENT_SAFE_LESSON_FIELDS}
        doc["source"] = f"LMS Course: {doc.get('course')}, Course Lesson: {doc.get('title')}"
        return doc

    reg.register(Tool("get_lesson_context", "Lấy nội dung bài học (đã lọc theo role, student không thấy instructor_notes).", {"type": "object", "properties": {"lesson": {"type": "string"}}, "required": ["lesson"]}, get_lesson_context))

    # ---------- 4. get_my_progress ----------
    def get_my_progress(a: dict):
        if _mock():
            return {"member": a.get("member"), "progress": 45, "current_lesson": "Bài 3", "source": "LMS Enrollment"}
        enr = frappe.list_documents("LMS Enrollment", filters={"course": a["course"], "member": a["member"]}, fields=["name", "progress", "current_lesson"], limit=1)
        prog = frappe.list_documents("LMS Course Progress", filters={"course": a["course"], "member": a["member"]}, fields=["lesson", "status"], limit=100)
        return {"enrollment": enr.get("data"), "details": prog.get("data"), "source": "LMS Enrollment + LMS Course Progress"}

    reg.register(Tool("get_my_progress", "Tiến độ của chính học viên (member tự ép từ context, không cho LLM giả).", {"type": "object", "properties": {"course": {"type": "string"}, "member": {"type": "string"}}, "required": ["course"]}, get_my_progress))

    # ---------- 5. get_batch_progress ----------
    def get_batch_progress(a: dict):
        if _mock():
            return {"batch": a.get("batch"), "enrolled": 30, "avg_progress": 52}
        return frappe.list_documents("LMS Batch Enrollment", filters={"batch": a["batch"]}, fields=["member", "course"], limit=100)

    reg.register(Tool("get_batch_progress", "Thống kê batch (teacher/admin).", {"type": "object", "properties": {"batch": {"type": "string"}}, "required": ["batch"]}, get_batch_progress))

    # ---------- 6. find_at_risk_students ----------
    def find_at_risk_students(a: dict):
        if _mock():
            return {"at_risk": [{"member": "hv1@test.com", "progress": 10}]}
        rows = frappe.list_documents("LMS Enrollment", filters={"course": a["course"]}, fields=["member", "progress"], limit=200).get("data", [])
        th = int(a.get("threshold", 30))
        return {"at_risk": [r for r in rows if (r.get("progress") or 0) < th]}

    reg.register(Tool("find_at_risk_students", "Tìm học viên progress thấp (teacher/admin).", {"type": "object", "properties": {"course": {"type": "string"}, "threshold": {"type": "integer", "default": 30}}, "required": ["course"]}, find_at_risk_students))

    # ---------- 7. submissions (evaluator) ----------
    def get_quiz_submissions(a: dict):
        if _mock():
            return {"data": []}
        return frappe.list_documents("LMS Quiz Submission", filters={"quiz": a["quiz"]}, fields=["member", "score", "percentage"], limit=50)

    reg.register(Tool("get_quiz_submissions", "Xem bài nộp quiz (evaluator/teacher).", {"type": "object", "properties": {"quiz": {"type": "string"}}, "required": ["quiz"]}, get_quiz_submissions))

    def get_assignment_submissions(a: dict):
        if _mock():
            return {"data": []}
        return frappe.list_documents("LMS Assignment Submission", filters={"assignment": a["assignment"]}, fields=["member", "status", "answer"], limit=50)

    reg.register(Tool("get_assignment_submissions", "Xem bài nộp assignment (evaluator/teacher).", {"type": "object", "properties": {"assignment": {"type": "string"}}, "required": ["assignment"]}, get_assignment_submissions))

    # ---------- 8. draft_quiz (chỉ nháp, không ghi Frappe) ----------
    def draft_quiz(a: dict):
        # Agent tạo nháp ngoài, giáo viên duyệt mới ghi -> tránh transaction nhiều DocType
        n = int(a.get("num_questions", 3))
        return {
            "draft": True,
            "lesson": a.get("lesson"),
            "topic": a.get("topic"),
            "questions": [{"question": f"Câu hỏi nháp {i+1} về {a.get('topic')}", "type": "Choices", "options": ["A", "B", "C", "D"]} for i in range(n)],
            "note": "Bản nháp, cần giáo viên duyệt. Ghi thật dùng update_course_content_after_approval.",
        }

    reg.register(Tool("draft_quiz", "Tạo quiz NHÁP (không ghi vào Frappe).", {"type": "object", "properties": {"lesson": {"type": "string"}, "topic": {"type": "string"}, "num_questions": {"type": "integer", "default": 3}}, "required": ["lesson", "topic"]}, draft_quiz))

    # ---------- 9. draft_assignment_feedback ----------
    def draft_assignment_feedback(a: dict):
        return {"draft": True, "submission": a.get("submission"), "feedback": a.get("points", "Làm tốt, cần bổ sung ví dụ."), "note": "Nháp, giáo viên duyệt mới lưu."}

    reg.register(Tool("draft_assignment_feedback", "Tạo feedback NHÁP cho bài nộp.", {"type": "object", "properties": {"submission": {"type": "string"}, "points": {"type": "string"}}, "required": ["submission"]}, draft_assignment_feedback))

    # ---------- 10. update_course_content_after_approval (ghi thật, cần duyệt) ----------
    def _write(a: dict):
        # MVP: chỉ update Lesson.quiz_id/body. Quiz nhiều bước atomic để cho agent_bridge sau.
        if _mock():
            return {"ok": True, "mock": True, "wrote": a}
        return frappe.update_document("Course Lesson", a["lesson"], {"body": a.get("body", "")})

    reg.register(Tool("update_course_content_after_approval", "GHI vào Frappe sau phê duyệt. Nguy hiểm.", {"type": "object", "properties": {"lesson": {"type": "string"}, "body": {"type": "string"}}, "required": ["lesson"]}, _write, needs_approval=True))

    return reg
