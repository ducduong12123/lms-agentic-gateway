"""Read-only contract smoke test against the configured Frappe LMS 2.62.1."""
from __future__ import annotations

import json
import sys

from gateway.config import settings
from gateway.connector.frappe_client import FrappeClient
from gateway.runtime.route_adapter import parse_lms_route
from gateway.tools.envelope import validate_view_spec
from gateway.tools.verbs_client import validate_route
DOCTYPES = (
    "LMS Course", "Course Lesson", "LMS Question", "LMS Quiz",
    "LMS Quiz Submission", "LMS Assignment Submission",
    "LMS Programming Exercise Submission", "LMS Course Progress",
    "LMS Video Watch Duration", "LMS Enrollment", "LMS Lesson Note",
    "LMS Live Class",
)


def main() -> int:
    frappe = FrappeClient(
        settings.frappe_url, settings.frappe_api_key, settings.frappe_api_secret,
        site_host=settings.frappe_site_host,
    )
    result = {"target": "Frappe LMS 2.62.1", "doctypes": {}, "route": False, "client": {}, "methods": {}}
    for doctype in DOCTYPES:
        try:
            frappe.list_documents(doctype, fields=["name"], limit=1)
            result["doctypes"][doctype] = "ok"
        except Exception as exc:
            result["doctypes"][doctype] = f"error:{type(exc).__name__}"
    result["route"] = parse_lms_route("/lms/courses/COURSE/learn/1-1")["kind"] == "lesson"
    try:
        result["client"]["route"] = validate_route("/lms/courses/COURSE/learn/1-1")
        result["client"]["views"] = all(
            validate_view_spec(spec)["type"] == spec["type"]
            for spec in (
                {"type": "course", "course": "COURSE"},
                {"type": "lesson", "course": "COURSE", "chapter": 1, "lesson_number": 1},
                {"type": "diff", "doctype": "Course", "name": "COURSE", "before": "", "after": ""},
            )
        )
    except Exception as exc:
        result["client"]["error"] = f"error:{type(exc).__name__}"
    try:
        courses = frappe.list_documents("LMS Course", fields=["name"], limit=1).get("data", [])
        if courses:
            course = courses[0]["name"]
            outline = frappe.get_method("lms.lms.utils.get_course_outline", course=course)
            result["methods"]["get_course_outline"] = "ok" if "message" in outline else "unexpected"
        else:
            result["methods"]["get_course_outline"] = "skipped:no-course"
    except Exception as exc:
        result["methods"]["get_course_outline"] = f"error:{type(exc).__name__}"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    values = list(result["doctypes"].values()) + list(result["methods"].values()) + list(result["client"].values())
    return 1 if not result["route"] or result["client"].get("views") is not True or any(str(value).startswith("error:") for value in values) else 0


if __name__ == "__main__":
    sys.exit(main())
