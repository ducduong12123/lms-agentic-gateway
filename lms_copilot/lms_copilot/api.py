"""HTTP API of LMS Copilot.

Two groups of endpoints:

* ``get_tools`` / ``call_tool`` for the AI Gateway, which acts as the widget's user;
* screen endpoints for the review queue and project submission pages.

Every endpoint requires a signed-in user. Every state change is a POST.
"""

import frappe

from lms_copilot.copilot import access, conversations, feedback, insights, proposals, queue, tools


@frappe.whitelist()
def get_tools():
	"""Tools available to the current user, with JSON schemas for function calling."""
	access.require_login()
	return {"audiences": sorted(access.audiences()), "tools": tools.catalog()}


@frappe.whitelist(methods=["POST"])
def call_tool(
	tool: str,
	arguments: dict | str | None = None,
	conversation: str | None = None,
	model: str | None = None,
	tokens_in: int | None = None,
	tokens_out: int | None = None,
):
	access.require_login()
	return tools.call(
		tool,
		arguments,
		conversation=conversation,
		model=model,
		tokens_in=tokens_in,
		tokens_out=tokens_out,
	)


@frappe.whitelist()
def get_session_context():
	"""Who the /copilot page is talking to and which screens they may open."""
	access.require_login()
	audiences = access.audiences()
	return {
		"user": frappe.session.user,
		"full_name": frappe.db.get_value("User", frappe.session.user, "full_name"),
		"is_teacher": access.TEACHER in audiences,
		# Datetimes are naive in the system time zone; the page measures ages against this.
		"server_now": frappe.utils.now_datetime(),
		"learner_widget": frappe.db.get_single_value("Copilot Settings", "enable_learner_widget"),
	}


# ------------------------------------------------------------------ teacher


@frappe.whitelist()
def get_review_queue(course: str | None = None, kind: str | None = None):
	return queue.get_review_queue(course=course, kind=kind)


@frappe.whitelist()
def get_proposal(name: str):
	access.require_login()
	return proposals.get_detail(name)


@frappe.whitelist(methods=["POST"])
def approve_proposal(name: str, params: dict | str | None = None, note: str | None = None):
	access.require_login()
	return proposals.approve(name, params=params, note=note)


@frappe.whitelist(methods=["POST"])
def reject_proposal(name: str, note: str | None = None):
	access.require_login()
	return proposals.reject(name, note=note)


@frappe.whitelist()
def get_feedback_review(draft: str):
	access.require_login()
	return feedback.get_feedback_review(draft)


@frappe.whitelist(methods=["POST"])
def save_feedback_draft(
	draft: str, scores: dict | str | None = None, message: str | None = None, note: str | None = None
):
	access.require_login()
	return feedback.save_feedback_draft(draft, scores=scores, message=message, note=note)


@frappe.whitelist(methods=["POST"])
def approve_feedback(
	draft: str,
	scores: dict | str | None = None,
	message: str | None = None,
	result_status: str | None = None,
	note: str | None = None,
):
	access.require_login()
	return feedback.approve_feedback(
		draft, scores=scores, message=message, result_status=result_status, note=note
	)


@frappe.whitelist(methods=["POST"])
def reject_feedback(draft: str, note: str | None = None):
	access.require_login()
	return feedback.reject_feedback(draft, note=note)


@frappe.whitelist(methods=["POST"])
def request_feedback_rewrite(draft: str, note: str):
	access.require_login()
	return feedback.request_rewrite(draft, note)


@frappe.whitelist(methods=["POST"])
def bulk_approve_feedback(drafts: list | str):
	access.require_login()
	return feedback.bulk_approve_feedback(drafts)


@frappe.whitelist()
def get_weekly_insight(course: str, week_start: str | None = None):
	access.require_login()
	return insights.get_weekly_insight(course, week_start=week_start)


# ------------------------------------------------------------------ learner


@frappe.whitelist()
def get_assignment(assignment: str):
	return feedback.get_assignment_for_learner(assignment)


@frappe.whitelist(methods=["POST"])
def submit_project(assignment: str, repo_url: str, lesson: str | None = None):
	return feedback.submit_project(assignment, repo_url, lesson=lesson)


@frappe.whitelist(methods=["POST"])
def rate_answer(conversation: str, message_index: int, helpful: bool | int | str):
	access.require_login()
	return conversations.rate_answer(conversation, message_index, helpful)
