"""Write tools never write. They create a Copilot Proposal that a teacher approves.

Lifecycle::

    Pending --approve--> Approved --execute+verify--> Applied
       |                    \\--error, rolled back---> Failed
       |--reject---> Rejected
       \\--expired or source changed--> Expired

On approval the reviewer's own permission is checked again, the source record
is compared with the version the agent read, and only then does the write run.
"""

import time

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, now_datetime

from lms_copilot.copilot import access
from lms_copilot.copilot.audit import log_tool
from lms_copilot.copilot.content import (
	append_block,
	content_hash,
	lesson_text,
	markdown_block,
	parse_editor,
	replace_block,
	text_diff,
)
from lms_copilot.copilot.quiz import create_quiz_in_lesson, normalise_quiz, quiz_preview_lines
from lms_copilot.copilot.validation import (
	boolean,
	confidence,
	existing,
	fail,
	integer,
	load_json,
	optional_text,
	parse_list,
	parse_object,
	required_text,
)

OPEN = "Pending"
MAX_MARKDOWN = 20000
MAX_REMINDER_LEARNERS = 200
MAX_RUBRIC_CRITERIA = 20
MAX_RUBRIC_LEVEL = 10


class ProposalType:
	"""One kind of write. Subclasses define how to validate, preview, run and verify it."""

	label = ""
	tool = ""
	audiences = frozenset()
	source_doctype = None
	# Keys a reviewer may not change while editing: they decide what is touched.
	locked_keys = ()

	def prepare(self, params):
		"""Validate params from the agent; return fields for the proposal."""
		raise NotImplementedError

	def normalise(self, params, proposal):
		"""Validate params again, including a reviewer's edits, before execution."""
		params = parse_object(params, _("Parameters"))
		original = load_json(proposal.params, {})
		params.update({key: original.get(key) for key in self.locked_keys})
		return self.prepare(params)["params"]

	def assert_reviewer(self, proposal):
		access.assert_teacher(proposal.course)

	def execute(self, proposal, params):
		raise NotImplementedError

	def verify(self, proposal, result):
		return True

	def source_state(self, name):
		if not self.source_doctype or not name:
			return None, None
		content = frappe.db.get_value(self.source_doctype, name, "content")
		version = frappe.db.get_value(self.source_doctype, name, "modified")
		return str(version), content_hash(content)


def _lesson_for_teacher(lesson):
	lesson = existing("Course Lesson", lesson, _("Lesson"))
	doc = frappe.get_doc("Course Lesson", lesson)
	if not access.is_engine():
		access.assert_teacher(doc.course)
	return doc


class LessonQuiz(ProposalType):
	label = "Lesson Quiz"
	tool = "propose_lesson_quiz"
	audiences = frozenset({access.TEACHER, access.ENGINE})
	source_doctype = "Course Lesson"
	locked_keys = ("lesson",)

	def prepare(self, params):
		params = parse_object(params, _("Parameters"))
		lesson = _lesson_for_teacher(params.get("lesson"))
		quiz = normalise_quiz(params.get("quiz"))
		return {
			"course": lesson.course,
			"reference_doctype": "Course Lesson",
			"reference_name": lesson.name,
			"title": _("Add quiz “{0}” to {1}").format(quiz["title"], lesson.title),
			"params": {"lesson": lesson.name, "quiz": quiz},
			"preview": {
				"kind": "diff",
				"lines": [{"op": "add", "text": line} for line in quiz_preview_lines(quiz)],
			},
		}

	def execute(self, proposal, params):
		return create_quiz_in_lesson(params["lesson"], params["quiz"])

	def verify(self, proposal, result):
		content = frappe.db.get_value("Course Lesson", result["lesson"], "content")
		blocks = parse_editor(content)["blocks"]
		return any(block.get("data", {}).get("quiz") == result["quiz"] for block in blocks) and bool(
			frappe.db.exists("LMS Quiz", result["quiz"])
		)


class LessonChange(ProposalType):
	label = "Lesson Change"
	tool = "propose_lesson_change"
	audiences = frozenset({access.TEACHER, access.ENGINE})
	source_doctype = "Course Lesson"
	locked_keys = ("lesson",)
	modes = ("append", "replace_block")

	def _apply(self, lesson, params):
		if params["mode"] == "append":
			return append_block(lesson.content, markdown_block(params["markdown"]), params.get("after_block"))
		return replace_block(lesson.content, params["block_id"], params["markdown"])

	def prepare(self, params):
		params = parse_object(params, _("Parameters"))
		lesson = _lesson_for_teacher(params.get("lesson"))
		mode = params.get("mode") or "append"
		if mode not in self.modes:
			fail(_("Mode must be one of: {0}.").format(", ".join(self.modes)))
		clean = {
			"lesson": lesson.name,
			"mode": mode,
			"markdown": required_text(params.get("markdown"), _("Lesson text"), MAX_MARKDOWN),
			"reason": optional_text(params.get("reason"), _("Reason"), 1000),
		}
		if mode == "append":
			clean["after_block"] = optional_text(params.get("after_block"), _("Block"), 40)
		else:
			clean["block_id"] = required_text(params.get("block_id"), _("Block"), 40)

		after = frappe.get_doc("Course Lesson", lesson.name)
		after.content = self._apply(lesson, clean)
		return {
			"course": lesson.course,
			"reference_doctype": "Course Lesson",
			"reference_name": lesson.name,
			"title": _("Edit lesson {0}").format(lesson.title),
			"summary": clean["reason"],
			"params": clean,
			"preview": {"kind": "diff", "lines": text_diff(lesson_text(lesson), lesson_text(after))},
		}

	def execute(self, proposal, params):
		lesson = frappe.get_doc("Course Lesson", params["lesson"])
		lesson.content = self._apply(lesson, params)
		lesson.save(ignore_permissions=True)
		return {"lesson": lesson.name, "course": lesson.course, "version": content_hash(lesson.content)}

	def verify(self, proposal, result):
		lesson = frappe.get_doc("Course Lesson", result["lesson"])
		markdown = (load_json(proposal.final_params, {}) or {}).get("markdown", "")
		blocks = parse_editor(lesson.content)["blocks"]
		return any(block.get("data", {}).get("text") == markdown for block in blocks)


def _notify(member, subject, message, course, from_user):
	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"for_user": member,
			"from_user": from_user,
			"type": "Alert",
			"subject": subject,
			"email_content": message,
			"document_type": "LMS Course",
			"document_name": course,
		}
	).insert(ignore_permissions=True)


class LearnerReminder(ProposalType):
	label = "Learner Reminder"
	tool = "propose_learner_reminder"
	audiences = frozenset({access.TEACHER, access.ENGINE})
	locked_keys = ("course",)

	def prepare(self, params):
		params = parse_object(params, _("Parameters"))
		course = existing("LMS Course", params.get("course"), _("Course"))
		if not access.is_engine():
			access.assert_teacher(course)
		refs = parse_list(params.get("learners"), _("Learners"), minimum=1, maximum=MAX_REMINDER_LEARNERS)
		refs = [required_text(ref, _("Learner"), 20) for ref in refs]
		access.resolve_learner_refs(course, refs)
		message = required_text(params.get("message"), _("Message"), 2000)
		title = frappe.db.get_value("LMS Course", course, "title")
		return {
			"course": course,
			"reference_doctype": "LMS Course",
			"reference_name": course,
			"title": _("Remind {0} learner(s) in {1}").format(len(refs), title),
			"summary": optional_text(params.get("reason"), _("Reason"), 1000),
			"params": {"course": course, "learners": refs, "message": message},
			"preview": {"kind": "message", "recipients": refs, "message": message},
		}

	def execute(self, proposal, params):
		members = access.resolve_learner_refs(params["course"], params["learners"])
		subject = _("A reminder from your teacher")
		for member in members:
			_notify(member, subject, params["message"], params["course"], frappe.session.user)
		return {"course": params["course"], "count": len(members)}

	def verify(self, proposal, result):
		return result["count"] == len(load_json(proposal.final_params, {}).get("learners", []))


class Escalation(ProposalType):
	"""A learner question the assistant must not answer. Approving means replying."""

	label = "Escalation"
	tool = "escalate_to_teacher"
	audiences = frozenset({access.LEARNER})

	def prepare(self, params):
		params = parse_object(params, _("Parameters"))
		course = existing("LMS Course", params.get("course"), _("Course"))
		access.assert_course_reader(course)
		lesson = params.get("lesson")
		if lesson:
			lesson = existing("Course Lesson", lesson, _("Lesson"))
		question = required_text(params.get("question"), _("Question"), 2000)
		return {
			"course": course,
			"reference_doctype": "Course Lesson" if lesson else "LMS Course",
			"reference_name": lesson or course,
			"title": question[:120],
			"summary": optional_text(params.get("summary"), _("Summary"), 2000),
			"params": {"course": course, "lesson": lesson, "question": question},
			"preview": {"kind": "question", "question": question},
		}

	def normalise(self, params, proposal):
		params = parse_object(params, _("Parameters"))
		original = load_json(proposal.params, {})
		return {**original, "reply": required_text(params.get("reply"), _("Reply"), 4000)}

	def assert_reviewer(self, proposal):
		access.assert_reviewer(proposal.course)

	def execute(self, proposal, params):
		_notify(
			proposal.requested_by,
			_("Your teacher answered: {0}").format(params["question"][:80]),
			params["reply"],
			params["course"],
			frappe.session.user,
		)
		return {"notified": proposal.requested_by, "course": params["course"]}


def _criterion(item, index, course):
	if not isinstance(item, dict):
		fail(_("Criterion {0} must be an object.").format(index))
	label = _("Criterion {0}").format(index)
	max_level = integer(item.get("max_level", 3), _("{0} max level").format(label), 1, MAX_RUBRIC_LEVEL)
	points = item.get("points")
	if points not in (None, ""):
		try:
			points = float(points)
		except (TypeError, ValueError):
			fail(_("{0} points must be a number.").format(label))
		if isinstance(item.get("points"), bool) or not 0 <= points <= 1000:
			fail(_("{0} points must be between 0 and 1000.").format(label))
	lesson = optional_text(item.get("taught_in_lesson"), _("{0} lesson").format(label), 140)
	if lesson:
		lesson = existing("Course Lesson", lesson, _("{0} lesson").format(label))
		if frappe.db.get_value("Course Lesson", lesson, "course") != course:
			fail(_("{0} points to a lesson from another course.").format(label))
	return {
		"criterion": required_text(item.get("criterion"), label, 140),
		"description": optional_text(item.get("description"), _("{0} description").format(label), 2000),
		"max_level": max_level,
		"points": points if points not in (None, "") else None,
		"levels": optional_text(item.get("levels"), _("{0} levels").format(label), 2000),
		"taught_in_lesson": lesson,
		"pass_example": optional_text(item.get("pass_example"), _("{0} passing example").format(label), 4000),
		"fail_example": optional_text(item.get("fail_example"), _("{0} failing example").format(label), 4000),
	}


def _rubric_lines(title, criteria):
	lines = [f"# {title}"] if title else []
	for row in criteria:
		points = f", {row['points']:g} pts" if row.get("points") else ""
		lines.append(f"## {row['criterion']} (1–{row['max_level']}{points})")
		for key, prefix in (
			("description", ""),
			("levels", ""),
			("taught_in_lesson", "Lesson: "),
			("pass_example", "Pass: "),
			("fail_example", "Fail: "),
		):
			if row.get(key):
				lines.extend(f"{prefix}{line}" for line in str(row[key]).splitlines())
	return "\n".join(lines)


def _current_rubric(assignment):
	name = frappe.db.get_value("Copilot Rubric", {"assignment": assignment}, "name", order_by="modified desc")
	return frappe.get_doc("Copilot Rubric", name) if name else None


def _rubric_rows(rubric):
	fields = (
		"criterion",
		"description",
		"max_level",
		"points",
		"levels",
		"taught_in_lesson",
		"pass_example",
		"fail_example",
	)
	return [{key: row.get(key) for key in fields} for row in rubric.criteria]


class Rubric(ProposalType):
	"""Create or replace the rubric an assignment is graded with."""

	label = "Rubric"
	tool = "propose_rubric"
	audiences = frozenset({access.TEACHER, access.ENGINE})
	source_doctype = "LMS Assignment"
	locked_keys = ("assignment",)

	def prepare(self, params):
		params = parse_object(params, _("Parameters"))
		assignment = existing("LMS Assignment", params.get("assignment"), _("Assignment"))
		doc = frappe.get_doc("LMS Assignment", assignment)
		course = doc.get("course")
		if not course:
			fail(_("This assignment is not linked to a course."))
		if not access.is_engine():
			access.assert_teacher(course)
		items = parse_list(params.get("criteria"), _("Criteria"), minimum=1, maximum=MAX_RUBRIC_CRITERIA)
		criteria = [_criterion(item, index, course) for index, item in enumerate(items, 1)]
		names = [row["criterion"] for row in criteria]
		if len(set(names)) != len(names):
			fail(_("Each criterion needs a different name."))
		title = optional_text(params.get("title"), _("Title"), 140) or doc.title
		visible = params.get("visible_to_learner")
		clean = {
			"assignment": assignment,
			"title": title,
			"criteria": criteria,
			"visible_to_learner": 1 if visible in (None, "") else boolean(visible, _("Visible to learners")),
			"notes": optional_text(params.get("notes"), _("Notes"), 4000),
			"reason": optional_text(params.get("reason"), _("Reason"), 1000),
		}
		current = _current_rubric(assignment)
		before = _rubric_lines(current.title, _rubric_rows(current)) if current else ""
		return {
			"course": course,
			"reference_doctype": "LMS Assignment",
			"reference_name": assignment,
			"title": (_("Replace rubric of {0}") if current else _("Add rubric to {0}")).format(doc.title),
			"summary": clean["reason"],
			"params": clean,
			"preview": {"kind": "diff", "lines": text_diff(before, _rubric_lines(title, criteria))},
		}

	def source_state(self, name):
		"""The rubric the agent saw: stale if it is created, edited or removed before approval."""
		if not name:
			return None, None
		current = _current_rubric(name)
		if not current:
			return None, content_hash("no rubric")
		state = frappe.as_json({"name": current.name, "title": current.title, "rows": _rubric_rows(current)})
		return str(current.modified), content_hash(state)

	def execute(self, proposal, params):
		rubric = _current_rubric(params["assignment"])
		values = {
			"title": params["title"],
			"visible_to_learner": params["visible_to_learner"],
			"criteria": params["criteria"],
		}
		if params.get("notes"):
			values["notes"] = params["notes"]
		if rubric:
			rubric.update(values)
			rubric.save(ignore_permissions=True)
		else:
			rubric = frappe.get_doc(
				{"doctype": "Copilot Rubric", "assignment": params["assignment"], **values}
			).insert(ignore_permissions=True)
		return {"rubric": rubric.name, "assignment": params["assignment"], "criteria": len(rubric.criteria)}

	def verify(self, proposal, result):
		expected = [row["criterion"] for row in load_json(proposal.final_params, {}).get("criteria", [])]
		rubric = _current_rubric(result["assignment"])
		return (
			bool(rubric)
			and rubric.name == result["rubric"]
			and [row.criterion for row in rubric.criteria] == expected
		)


PROPOSAL_TYPES = {
	handler.label: handler
	for handler in (LessonQuiz(), LessonChange(), LearnerReminder(), Escalation(), Rubric())
}
TOOL_TYPES = {handler.tool: handler for handler in PROPOSAL_TYPES.values()}


def _ttl_days():
	days = frappe.db.get_single_value("Copilot Settings", "proposal_ttl_days")
	return days if days and days > 0 else 7


def _requested_via():
	if access.is_engine():
		return "AI Engine"
	if access.TEACHER in access.audiences():
		return "Teacher"
	return "Learner"


def _existing_or_none(doctype, name):
	return name if name and frappe.db.exists(doctype, name) else None


def create_proposal(tool, params, conversation=None, insight=None):
	"""Validate a write request and store it as a pending proposal."""
	handler = TOOL_TYPES[tool]
	params = parse_object(params, _("Parameters"))
	prepared = handler.prepare(params)
	version, source_hash = handler.source_state(prepared.get("reference_name"))
	if handler.source_doctype and prepared.get("reference_doctype") != handler.source_doctype:
		version, source_hash = None, None
	doc = frappe.get_doc(
		{
			"doctype": "Copilot Proposal",
			"proposal_type": handler.label,
			"tool": tool,
			"status": OPEN,
			"title": prepared.get("title"),
			"summary": prepared.get("summary"),
			"course": prepared.get("course"),
			"reference_doctype": prepared.get("reference_doctype"),
			"reference_name": prepared.get("reference_name"),
			"params": frappe.as_json(prepared["params"]),
			"preview": frappe.as_json(prepared.get("preview") or {}),
			"source_version": version,
			"source_hash": source_hash,
			"confidence": confidence(params.get("confidence"), _("Confidence")),
			"requested_by": frappe.session.user,
			"requested_via": _requested_via(),
			"conversation": _existing_or_none("Copilot Conversation", conversation),
			"insight": _existing_or_none("Copilot Weekly Insight", insight),
			"expires_on": add_days(now_datetime(), _ttl_days()),
		}
	)
	doc.insert(ignore_permissions=True)
	return {
		"proposal": doc.name,
		"status": doc.status,
		"title": doc.title,
		"message": _("Drafted for teacher approval. Nothing has been changed yet."),
	}


def _get_open(name):
	name = existing("Copilot Proposal", name, _("Proposal"))
	frappe.db.get_value("Copilot Proposal", name, "name", for_update=True)
	return frappe.get_doc("Copilot Proposal", name)


def is_stale(doc):
	handler = PROPOSAL_TYPES[doc.proposal_type]
	if not doc.source_hash:
		return False
	_version, current_hash = handler.source_state(doc.reference_name)
	return current_hash != doc.source_hash


def is_expired(doc):
	return bool(doc.expires_on) and get_datetime(doc.expires_on) < now_datetime()


def _close(doc, status, **values):
	doc.status = status
	doc.update(values)
	doc.save(ignore_permissions=True)


def expire_if_needed(doc):
	if doc.status != OPEN:
		return False
	if is_expired(doc):
		_close(doc, "Expired", error=_("The proposal passed its review deadline."))
		return True
	if is_stale(doc):
		_close(doc, "Expired", error=_("The lesson changed after the assistant read it."))
		return True
	return False


def approve(name, params=None, note=None):
	"""Approve and apply a proposal as the signed-in reviewer."""
	started = time.monotonic()
	doc = _get_open(name)
	handler = PROPOSAL_TYPES[doc.proposal_type]
	handler.assert_reviewer(doc)
	if doc.status != OPEN:
		frappe.throw(_("This proposal is already {0}.").format(_(doc.status)), frappe.ValidationError)
	if expire_if_needed(doc):
		return {"proposal": doc.name, "status": doc.status, "error": doc.error}

	edited = params not in (None, "", {})
	final_params = handler.normalise(params if edited else load_json(doc.params, {}), doc)
	doc.update(
		{
			"status": "Approved",
			"final_params": frappe.as_json(final_params),
			"reviewed_by": frappe.session.user,
			"reviewed_on": now_datetime(),
			"review_note": note,
		}
	)
	doc.save(ignore_permissions=True)

	savepoint = f"copilot_{frappe.generate_hash(length=8)}"
	frappe.db.savepoint(savepoint)
	try:
		result = handler.execute(doc, final_params)
		if not handler.verify(doc, result):
			fail(_("The change could not be verified after it was applied."))
	except Exception as error:
		frappe.db.rollback(save_point=savepoint)
		doc.reload()
		_close(doc, "Failed", error=str(error)[:1000])
		log_tool(
			f"apply:{doc.tool}",
			"Error",
			arguments={"proposal": doc.name, "edited": edited},
			error=error,
			duration_ms=(time.monotonic() - started) * 1000,
			is_write=True,
			proposal=doc.name,
		)
		return {"proposal": doc.name, "status": doc.status, "error": doc.error}

	_close(doc, "Applied", result=frappe.as_json(result))
	log_tool(
		f"apply:{doc.tool}",
		"Success",
		arguments={"proposal": doc.name, "edited": edited},
		result=result,
		duration_ms=(time.monotonic() - started) * 1000,
		is_write=True,
		proposal=doc.name,
	)
	return {"proposal": doc.name, "status": doc.status, "result": result}


def reject(name, note=None):
	doc = _get_open(name)
	PROPOSAL_TYPES[doc.proposal_type].assert_reviewer(doc)
	if doc.status != OPEN:
		frappe.throw(_("This proposal is already {0}.").format(_(doc.status)), frappe.ValidationError)
	_close(
		doc,
		"Rejected",
		reviewed_by=frappe.session.user,
		reviewed_on=now_datetime(),
		review_note=optional_text(note, _("Note"), 2000),
	)
	return {"proposal": doc.name, "status": doc.status}


def get_detail(name):
	name = existing("Copilot Proposal", name, _("Proposal"))
	doc = frappe.get_doc("Copilot Proposal", name)
	PROPOSAL_TYPES[doc.proposal_type].assert_reviewer(doc)
	expire_if_needed(doc)
	return {
		"name": doc.name,
		"type": doc.proposal_type,
		"tool": doc.tool,
		"status": doc.status,
		"title": doc.title,
		"summary": doc.summary,
		"course": doc.course,
		"course_title": frappe.db.get_value("LMS Course", doc.course, "title") if doc.course else None,
		"reference_doctype": doc.reference_doctype,
		"reference_name": doc.reference_name,
		"params": load_json(doc.params, {}),
		"final_params": load_json(doc.final_params),
		"preview": load_json(doc.preview, {}),
		"confidence": doc.confidence,
		"requested_by": doc.requested_by,
		"requested_by_name": frappe.db.get_value("User", doc.requested_by, "full_name"),
		"requested_via": doc.requested_via,
		"created": doc.creation,
		"expires_on": doc.expires_on,
		"reviewed_by": doc.reviewed_by,
		"reviewed_on": doc.reviewed_on,
		"result": load_json(doc.result),
		"error": doc.error,
	}


def expire_open_proposals():
	"""Daily: close proposals whose deadline passed or whose source changed."""
	for name in frappe.get_all("Copilot Proposal", filters={"status": OPEN}, pluck="name"):
		expire_if_needed(frappe.get_doc("Copilot Proposal", name))
