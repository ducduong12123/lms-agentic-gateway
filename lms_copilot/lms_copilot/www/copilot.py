import re
from pathlib import Path

import frappe
from frappe.translate import get_all_translations

no_cache = 1

SCRIPT = Path(__file__).resolve().parent.parent / "public" / "js" / "copilot.js"
# Values the page translates at runtime with __(value): statuses, results, stats.
DYNAMIC_MESSAGES = {
	"Pending",
	"Pending Review",
	"Approved",
	"Applied",
	"Rejected",
	"Expired",
	"Superseded",
	"Failed",
	"Feedback Sent",
	"Awaiting Review",
	"Testing",
	"Rewrite Requested",
	"Error",
	"Pass",
	"Fail",
	"Not Graded",
	"Unchanged",
	"Light",
	"Rewrite",
	"Teacher",
	"Learner",
	"AI Engine",
	"learners",
	"questions",
	"submissions",
	"approved_feedback",
}
MESSAGE_CALL = re.compile(r"""__\(\s*(['"])((?:\\.|(?!\1).)*)\1""")


def _source_messages():
	return {
		match.group(2).replace("\\'", "'").replace('\\"', '"')
		for match in MESSAGE_CALL.finditer(SCRIPT.read_text("utf-8"))
	}


def page_messages(lang):
	"""Only the translations copilot.js uses, so the page stays small."""
	key = f"lms_copilot:page_messages:{frappe.get_attr('lms_copilot.__version__')}:{lang}"
	cached = frappe.cache.get_value(key)
	if cached is not None:
		return cached
	translations = get_all_translations(lang) or {}
	sources = _source_messages() | DYNAMIC_MESSAGES
	messages = {source: translations[source] for source in sources if source in translations}
	frappe.cache.set_value(key, messages, expires_in_sec=3600)
	return messages


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=" + frappe.request.path
		raise frappe.Redirect(302)  # temporary: a cached 301 would outlive the login

	context.no_cache = 1
	context.title = "Learning Copilot"
	context.csrf_token = frappe.sessions.get_csrf_token()
	context.lang = frappe.local.lang or "en"
	# Inlined in a <script>: never let a translation close the tag.
	context.messages = frappe.as_json(page_messages(context.lang), indent=None).replace("</", "<\\/")
	context.asset_version = frappe.get_attr("lms_copilot.__version__")
	return context
