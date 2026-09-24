app_name = "lms_copilot"
app_title = "LMS Copilot"
app_publisher = "LMS Copilot contributors"
app_description = "Agentic Learning Copilot tools, proposals and approval flow for Frappe Learning"
app_email = "ainextx@gmail.com"
app_license = "MIT"

required_apps = ["lms"]

after_install = "lms_copilot.install.after_install"
after_migrate = "lms_copilot.install.after_migrate"

# The /copilot page is a standalone surface for the review queue and project
# submission. It never injects UI into the LMS SPA.
website_route_rules = [
	{"from_route": "/copilot/<path:app_path>", "to_route": "copilot"},
]

scheduler_events = {
	"daily": [
		"lms_copilot.copilot.proposals.expire_open_proposals",
		"lms_copilot.copilot.conversations.purge_old_transcripts",
	],
}

# Course Creator is scoped to courses where they are an instructor. These
# hooks keep direct /api/resource access consistent with the copilot API.
permission_query_conditions = {
	"Copilot Feedback Draft": "lms_copilot.permissions.feedback_draft_query",
	"Copilot Project Submission": "lms_copilot.permissions.project_submission_query",
	"Copilot Proposal": "lms_copilot.permissions.proposal_query",
	"Copilot Weekly Insight": "lms_copilot.permissions.weekly_insight_query",
	"Copilot Rubric": "lms_copilot.permissions.rubric_query",
}

has_permission = {
	"Copilot Feedback Draft": "lms_copilot.permissions.course_has_permission",
	"Copilot Project Submission": "lms_copilot.permissions.course_has_permission",
	"Copilot Proposal": "lms_copilot.permissions.course_has_permission",
	"Copilot Weekly Insight": "lms_copilot.permissions.course_has_permission",
	"Copilot Rubric": "lms_copilot.permissions.rubric_has_permission",
}
