"""Business tools duy nhất LLM được thấy. Map đúng DocType thật đã kiểm."""
from __future__ import annotations

from ..connector.frappe_client import FrappeClient
from ..runtime import learner as _learner
from ..runtime import features as _features
from ..runtime.long_memory import add_memory as _add_memory
from ..runtime.long_memory import forget_memory as _forget_memory
from ..runtime.long_memory import search_memories as _search_memories
from ..runtime.tool_registry import Tool, ToolRegistry
from .verbs_student import register as register_student_verbs
from .verbs_client import register as register_client_verbs
from .verbs_teacher import register as register_teacher_verbs
from .verbs_course_authoring import register as register_course_authoring_verbs

STUDENT_SAFE_LESSON_FIELDS = ["name", "title", "body", "content", "quiz_id", "course", "chapter", "include_in_preview"]


def _mock(frappe: FrappeClient) -> bool:
    # Chưa cấu hình key thì trả mock để demo runtime/widget chạy được
    return not frappe.is_configured


def build_registry(frappe: FrappeClient, llm=None) -> ToolRegistry:
    reg = ToolRegistry()

    # ---------- 1. search_courses ----------
    def search_courses(a: dict):
        q = a.get("query", "")
        if _mock(frappe):
            return {"data": [{"name": "PY-101", "title": "Python cơ bản", "source": "LMS Course: Python cơ bản"}]}
        res = frappe.list_documents("LMS Course", filters=[["title", "like", f"%{q}%"]], fields=["name", "title", "short_introduction"], limit=int(a.get("limit", 5)))
        return res

    reg.register(Tool("search_courses", "Tìm khóa học theo tên. Chỉ đọc.", {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 5}}, "required": ["query"]}, search_courses))

    # ---------- 2. get_course_outline ----------
    def get_course_outline(a: dict):
        course = a["course"]
        if _mock(frappe):
            return {"course": course, "chapters": [{"id": "CH-1", "title": "Ch1", "lessons": ["Bài 1", "Bài 2"]}], "source": f"LMS Course: {course}"}
        doc = frappe.get_document("LMS Course", course).get("data", {})
        out = {"course": doc.get("title"), "course_id": doc.get("name"), "chapters": []}
        for ch_ref in doc.get("chapters", []):
            chapter_id = ch_ref.get("chapter")
            ch = frappe.get_document("Course Chapter", chapter_id).get("data", {})
            lessons = []
            for lr in ch.get("lessons", []):
                lessons.append(lr.get("lesson"))
            out["chapters"].append({"id": ch.get("name") or chapter_id, "title": ch.get("title"), "lessons": lessons})
        out["source"] = f"LMS Course: {doc.get('title')}"
        return out
    reg.register(Tool("get_course_outline", "Lấy outline Course -> Chapter -> Lesson. Trả về course_id và chapters[].id (Course Chapter name) + lessons[] (Course Lesson name). Dùng chapter id khi tạo bài mới bằng publish_lesson_draft. Chỉ đọc.", {"type": "object", "properties": {"course": {"type": "string", "description": "LMS Course name/id, ví dụ 'kh-a-h-c-m-i'"}}, "required": ["course"]}, get_course_outline))

    # ---------- 3. get_lesson_context (student-safe) ----------
    def get_lesson_context(a: dict):
        lesson = a["lesson"]
        role = a.get("_role", "student")
        if _mock(frappe):
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
        if _mock(frappe):
            return {"member": a.get("member"), "progress": 45, "current_lesson": "Bài 3", "source": "LMS Enrollment"}
        enr = frappe.list_documents("LMS Enrollment", filters={"course": a["course"], "member": a["member"]}, fields=["name", "progress", "current_lesson"], limit=1)
        prog = frappe.list_documents("LMS Course Progress", filters={"course": a["course"], "member": a["member"]}, fields=["lesson", "status"], limit=100)
        return {"enrollment": enr.get("data"), "details": prog.get("data"), "source": "LMS Enrollment + LMS Course Progress"}

    reg.register(Tool("get_my_progress", "Tiến độ của chính học viên (member tự ép từ context, không cho LLM giả).", {"type": "object", "properties": {"course": {"type": "string"}, "member": {"type": "string"}}, "required": ["course"]}, get_my_progress))

    # ---------- 5. get_batch_progress ----------
    def get_batch_progress(a: dict):
        if _mock(frappe):
            return {"batch": a.get("batch"), "enrolled": 30, "avg_progress": 52}
        return frappe.list_documents("LMS Batch Enrollment", filters={"batch": a["batch"]}, fields=["member", "course"], limit=100)

    reg.register(Tool("get_batch_progress", "Thống kê batch (teacher/admin).", {"type": "object", "properties": {"batch": {"type": "string"}}, "required": ["batch"]}, get_batch_progress))

    # ---------- 6. find_at_risk_students ----------
    def find_at_risk_students(a: dict):
        if _mock(frappe):
            return {"at_risk": [{"member": "hv1@test.com", "progress": 10}]}
        rows = frappe.list_documents("LMS Enrollment", filters={"course": a["course"]}, fields=["member", "progress"], limit=200).get("data", [])
        th = int(a.get("threshold", 30))
        return {"at_risk": [r for r in rows if (r.get("progress") or 0) < th]}


    # ---------- 6b. list_at_risk_students (ITS engine, teacher/admin) ----------
    def list_at_risk_students(a: dict):
        role = str(a.get("_role") or "")
        if role not in ("teacher", "admin"):
            return {"error": "tool 'list_at_risk_students' chi danh cho teacher/admin"}
        course = str(a.get("course") or "")
        try:
            limit = int(a.get("limit", 10) or 10)
        except (TypeError, ValueError):
            limit = 10
        return {
            "course": course,
            "students": _features.risk_overview(course, limit),
            "source": "engine ITS evidence/mastery",
        }

    reg.register(Tool("list_at_risk_students", "Liệt kê học viên nguy cơ kèm concept yếu và vì sao (nguồn ITS engine). course để trống vẫn được; yếu nhất xếp trước. Dùng cho câu hỏi 'học viên yếu nhất là ai, vì sao' (teacher/admin).", {"type": "object", "properties": {"course": {"type": "string", "default": ""}, "limit": {"type": "integer", "default": 10}}, "required": []}, list_at_risk_students))

    # ---------- 6c. get_student_mastery (ITS engine, teacher/admin) ----------
    def get_student_mastery(a: dict):
        role = str(a.get("_role") or "")
        if role not in ("teacher", "admin"):
            return {"error": "tool 'get_student_mastery' chi danh cho teacher/admin"}
        student = str(a.get("student") or a.get("target") or "").strip()
        if not student or student == "Guest":
            return {"error": "thieu student."}
        course = str(a.get("course") or "")
        return {
            "student": student,
            "weak_concepts": _learner.weak_concepts(student, course, limit=5),
            "source": "engine ITS mastery",
        }

    reg.register(Tool("get_student_mastery", "Xem concept yếu kèm evidence của MỘT học viên cụ thể (teacher/admin). Tham số student là email/username học viên, không dùng member. Gọi sau list_at_risk_students để lấy vì sao chi tiết.", {"type": "object", "properties": {"student": {"type": "string", "description": "email/username học viên"}, "course": {"type": "string", "default": ""}}, "required": ["student"]}, get_student_mastery))

    # ---------- 7. submissions (evaluator) ----------
    def get_quiz_submissions(a: dict):
        if _mock(frappe):
            return {"data": []}
        return frappe.list_documents("LMS Quiz Submission", filters={"quiz": a["quiz"]}, fields=["member", "score", "percentage"], limit=50)

    reg.register(Tool("get_quiz_submissions", "Xem bài nộp quiz (evaluator/teacher).", {"type": "object", "properties": {"quiz": {"type": "string"}}, "required": ["quiz"]}, get_quiz_submissions))

    def get_assignment_submissions(a: dict):
        if _mock(frappe):
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
        if _mock(frappe):
            return {"ok": True, "mock": True, "wrote": a}
        return frappe.update_document("Course Lesson", a["lesson"], {"body": a.get("body", "")})

    reg.register(Tool("update_course_content_after_approval", "GHI vào Frappe sau phê duyệt. Nguy hiểm.", {"type": "object", "properties": {"lesson": {"type": "string"}, "body": {"type": "string"}}, "required": ["lesson"]}, _write, needs_approval=True))

    # ---------- 11. remember_user_fact (long-term memory, scope theo user) ----------
    def remember_user_fact(a: dict):
        # member ép từ context để LLM không ghi nhầm user khác; Guest bị chặn ở engine.
        return _add_memory(
            user=str(a.get("member") or ""),
            text=str(a.get("text") or ""),
            title=str(a.get("title") or ""),
            kind=str(a.get("kind") or "user_fact"),
            importance=float(a.get("importance", 0.8) or 0.8),
            confidence=float(a.get("confidence", 0.9) or 0.9),
        )

    reg.register(Tool(
        "remember_user_fact",
        "Lưu một thông tin bền vững về người học (tên, mục tiêu, trình độ, sở thích học). KHÔNG lưu bí mật/mật khẩu/OTP. Chỉ gọi khi thông tin rõ ràng, bền vững.",
        {"type": "object", "properties": {
            "text": {"type": "string", "description": "Nội dung cần nhớ, tối đa 2000 ký tự"},
            "title": {"type": "string", "default": ""},
            "kind": {"type": "string", "default": "user_fact"},
            "importance": {"type": "number", "default": 0.8},
            "confidence": {"type": "number", "default": 0.9},
        }, "required": ["text"]},
        remember_user_fact,
    ))

    # ---------- 12. recall_user_facts (long-term memory, scope theo user) ----------
    def recall_user_facts(a: dict):
        return {"memories": _search_memories(
            user=str(a.get("member") or ""),
            query=str(a.get("query") or ""),
            top_k=int(a.get("top_k", 4) or 4),
        )}

    reg.register(Tool(
        "recall_user_facts",
        "Tìm ký ức dài hạn của chính người học. Gọi khi câu hỏi cần sở thích/mục tiêu/trình độ đã nói trước đây.",
        {"type": "object", "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "default": 4},
        }, "required": ["query"]},
        recall_user_facts,
    ))

    # ---------- 13. forget_user_fact (long-term memory, soft-delete) ----------
    def forget_user_fact(a: dict):
        ok = _forget_memory(user=str(a.get("member") or ""), memory_id=str(a.get("memory_id") or ""))
        return {"ok": ok, "memory_id": str(a.get("memory_id") or "")}

    reg.register(Tool(
        "forget_user_fact",
        "Quên một ký ức theo id khi người học yêu cầu xóa/quên thông tin đó.",
        {"type": "object", "properties": {"memory_id": {"type": "string"}}, "required": ["memory_id"]},
        forget_user_fact,
    ))

    # ---------- 14. get_my_mastery (ITS learner model, member cưỡng bức) ----------
    def get_my_mastery(a: dict):
        member = str(a.get("member") or "")
        course = str(a.get("course") or "")
        return {
            "weak_concepts": _learner.weak_concepts(member, course, limit=3),
            "lesson_concepts": _learner.concepts_for_lesson(str(a.get("lesson") or "")),
        }

    reg.register(Tool(
        "get_my_mastery",
        "Xem concept yếu đã duyệt của chính người học, kèm vì sao. Dùng trước khi gợi ý ôn tập.",
        {"type": "object", "properties": {
            "course": {"type": "string", "default": ""},
            "lesson": {"type": "string", "default": ""},
        }},
        get_my_mastery,
    ))

    # ---------- 15. record_feedback_correction (ITS feedback, giảm nhiễu concept sai) ----------
    def record_feedback_correction(a: dict):
        member = str(a.get("member") or "")
        concept_id = str(a.get("concept_id") or "")
        return _features.record_concept_feedback(
            member, concept_id, str(a.get("note") or "not-accurate")
        )

    reg.register(Tool(
        "record_feedback_correction",
        "Ghi nhận khi học viên nói gợi ý concept là không đúng, để hiệu chỉnh learner model.",
        {"type": "object", "properties": {
            "concept_id": {"type": "string"},
            "note": {"type": "string", "default": "not-accurate"},
        }, "required": ["concept_id"]},
        record_feedback_correction,
    ))
    register_teacher_verbs(reg, frappe)
    register_course_authoring_verbs(reg, frappe)
    register_client_verbs(reg)
    register_student_verbs(reg, frappe, llm)
    return reg
