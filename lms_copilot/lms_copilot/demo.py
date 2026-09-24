"""Demo data for the review screens: ``bench --site <site> execute lms_copilot.demo.seed``.

Creates a small "Todo app" course with a rubric, three project submissions with
drafted feedback, a lesson-change proposal and an escalated question. Everything
it creates is tagged with DEMO_TAG and removed by ``lms_copilot.demo.clear``.
"""

import frappe

from lms_copilot.copilot import feedback, proposals

DEMO_TAG = "copilot-demo"
COURSE_TITLE = "React K12 (Copilot demo)"
REPO = "https://github.com/tastejs/todomvc"
LESSON_CONTENT = {
	"blocks": [
		{"id": "intro", "type": "header", "data": {"text": "useEffect và vòng đời component", "level": 2}},
		{
			"id": "deps",
			"type": "header",
			"data": {"text": "Mảng dependency", "level": 3},
		},
		{
			"id": "deps-body",
			"type": "markdown",
			"data": {"text": "Effect chạy lại khi một giá trị trong mảng dependency thay đổi."},
		},
		{"id": "cleanup", "type": "markdown", "data": {"text": "Trả về một hàm để dọn dẹp effect."}},
	],
	"version": "2.29.0",
}
LEARNERS = [
	("Nguyễn Văn", "An", "Medium"),
	("Trần Thị", "Bình", "High"),
	("Lê Hoàng", "Châu", "Low"),
]


def _user(email, first, last, roles):
	if frappe.db.exists("User", email):
		return email
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": first,
			"last_name": last,
			"send_welcome_email": 0,
			"user_type": "Website User",
			"roles": [{"role": role} for role in roles],
		}
	)
	user.insert(ignore_permissions=True)
	return email


def seed():
	if frappe.db.exists("LMS Course", {"title": COURSE_TITLE}):
		print("Demo already exists. Run lms_copilot.demo.clear first.")
		return
	frappe.set_user("Administrator")
	course = frappe.get_doc(
		{
			"doctype": "LMS Course",
			"title": COURSE_TITLE,
			"short_introduction": DEMO_TAG,
			"description": "Demo course for the Learning Copilot review screens.",
			"published": 1,
			"instructors": [{"instructor": "Administrator"}],
		}
	).insert(ignore_permissions=True)
	chapter = frappe.get_doc(
		{"doctype": "Course Chapter", "course": course.name, "title": "Chương 2: Hooks"}
	).insert(ignore_permissions=True)
	lesson = frappe.get_doc(
		{
			"doctype": "Course Lesson",
			"course": course.name,
			"chapter": chapter.name,
			"title": "Bài 5: useEffect",
			"content": frappe.as_json(LESSON_CONTENT),
		}
	).insert(ignore_permissions=True)
	assignment = frappe.get_doc(
		{
			"doctype": "LMS Assignment",
			"title": "Bài dự án 3: Todo app",
			"question": "<p>Xây dựng Todo app với React: thêm, sửa, xóa và lọc công việc.</p>",
			"type": "URL",
			"course": course.name,
			"grade_assignment": 1,
		}
	).insert(ignore_permissions=True)
	frappe.get_doc(
		{
			"doctype": "Copilot Rubric",
			"title": "Todo app v2",
			"assignment": assignment.name,
			"visible_to_learner": 1,
			"notes": DEMO_TAG,
			"criteria": [
				{"criterion": "Chức năng", "description": "Đủ thêm, sửa, xóa, lọc", "max_level": 3},
				{
					"criterion": "Dùng hook đúng cách",
					"description": "useEffect có dependency, không lặp",
					"max_level": 3,
				},
				{"criterion": "Cấu trúc code", "description": "Tách component, đặt tên rõ", "max_level": 3},
			],
		}
	).insert(ignore_permissions=True)

	for index, (first, last, confidence) in enumerate(LEARNERS, 1):
		email = _user(f"copilot-demo-{index}@example.com", first, last, ["LMS Student"])
		frappe.get_doc({"doctype": "LMS Enrollment", "course": course.name, "member": email}).insert(
			ignore_permissions=True
		)
		frappe.set_user(email)
		project = feedback.submit_project(assignment.name, REPO, lesson=lesson.name)["project_submission"]
		frappe.set_user("Administrator")
		feedback.record_submission_tests(
			project,
			[
				{"name": "thêm todo mới", "passed": True},
				{"name": "đánh dấu hoàn thành", "passed": True},
				{"name": "xóa todo cập nhật giao diện", "passed": confidence == "High"},
				{
					"name": "không gọi API lặp vô hạn",
					"passed": confidence == "High",
					"message": "fetch gọi 57 lần",
				},
			],
		)
		feedback.propose_feedback(
			project,
			[
				{
					"criterion": "Chức năng",
					"level": 3 if confidence == "High" else 2,
					"reason": "Xóa todo không cập nhật giao diện (test “xóa todo” trượt).",
					"confidence": "High",
					"citations": [{"file": "readme.md", "line_start": 3, "line_end": 4}],
				},
				{
					"criterion": "Dùng hook đúng cách",
					"level": 3 if confidence == "High" else 1,
					"reason": "useEffect thiếu mảng dependency nên gọi API sau mỗi lần render.",
					"confidence": confidence,
					"citations": [
						{"file": "readme.md", "line_start": 8, "line_end": 10},
						{"lesson": lesson.name, "block_id": "deps", "label": "Bài 5 · Mảng dependency"},
					],
				},
				{
					"criterion": "Cấu trúc code",
					"level": 3,
					"reason": "Tách component hợp lý, đặt tên rõ.",
					"confidence": "High",
				},
			],
			f"Chào {last}, bài của em đã chạy được phần thêm và đánh dấu hoàn thành. "
			"Có 2 chỗ cần sửa: (1) em đang sửa trực tiếp mảng todos, React sẽ không nhận ra thay đổi, "
			"em thử tạo mảng mới với filter; (2) xem lại Bài 5 về dependency của useEffect.",
			model="demo",
		)

	proposals.create_proposal(
		"propose_lesson_change",
		{
			"lesson": lesson.name,
			"after_block": "deps-body",
			"markdown": "### Ví dụ: quên dependency\nKhi không có mảng dependency, effect chạy sau mỗi lần "
			"render, và mỗi lần setState lại gây render mới.\n\n**Bài luyện:** sửa lỗi gọi API lặp vô hạn.",
			"reason": "14 học viên vướng lỗi này trong tuần 38.",
			"confidence": "High",
		},
	)
	frappe.set_user("copilot-demo-3@example.com")
	proposals.create_proposal(
		"escalate_to_teacher",
		{
			"course": course.name,
			"question": "Em có được nộp muộn vì ốm không ạ?",
			"summary": "Học viên hỏi về hạn nộp bài dự án 3.",
		},
	)
	frappe.set_user("Administrator")
	frappe.db.commit()
	print(f"Demo ready: /copilot  (course {course.name}, assignment {assignment.name})")


def clear():
	frappe.set_user("Administrator")
	course = frappe.db.get_value("LMS Course", {"title": COURSE_TITLE}, "name")
	if not course:
		print("No demo data.")
		return
	for doctype in ("Copilot Feedback Draft", "Copilot Project Submission", "Copilot Proposal"):
		for name in frappe.get_all(doctype, filters={"course": course}, pluck="name"):
			frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
	assignments = frappe.get_all("LMS Assignment", filters={"course": course}, pluck="name")
	for assignment in assignments:
		for name in frappe.get_all("Copilot Rubric", filters={"assignment": assignment}, pluck="name"):
			frappe.delete_doc("Copilot Rubric", name, force=True, ignore_permissions=True)
		for name in frappe.get_all(
			"LMS Assignment Submission", filters={"assignment": assignment}, pluck="name"
		):
			frappe.delete_doc("LMS Assignment Submission", name, force=True, ignore_permissions=True)
		frappe.delete_doc("LMS Assignment", assignment, force=True, ignore_permissions=True)
	for name in frappe.get_all("LMS Enrollment", filters={"course": course}, pluck="name"):
		frappe.delete_doc("LMS Enrollment", name, force=True, ignore_permissions=True)
	for doctype in ("Course Lesson", "Course Chapter"):
		for name in frappe.get_all(doctype, filters={"course": course}, pluck="name"):
			frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
	frappe.delete_doc("LMS Course", course, force=True, ignore_permissions=True)
	for index in range(1, len(LEARNERS) + 1):
		email = f"copilot-demo-{index}@example.com"
		if frappe.db.exists("User", email):
			frappe.delete_doc("User", email, force=True, ignore_permissions=True)
	frappe.db.commit()
	print("Demo data removed.")
