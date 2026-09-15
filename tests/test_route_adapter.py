from gateway.runtime.route_adapter import load_lesson_context, parse_lms_route, resolve_lms_context


def test_lesson_route_contract():
    assert parse_lms_route("/lms/courses/PY-101/learn/2-3?x=1") == {
        "kind": "lesson", "course": "PY-101", "chapter": 2,
        "lesson_number": 3, "path": "/lms/courses/PY-101/learn/2-3",
    }


def test_route_resolution_uses_permission_aware_lms_method():
    class Frappe:
        def get_method(self, dotted, **kwargs):
            assert dotted == "lms.lms.utils.get_lesson"
            assert kwargs == {"course": "PY-101", "chapter": 2, "lesson": 3}
            return {"message": {"name": "LESSON-9", "title": "Loops", "course": "PY-101"}}

    route = resolve_lms_context(Frappe(), "/lms/courses/PY-101/learn/2-3")
    assert route["lesson"] == "LESSON-9"
    assert route["lesson_title"] == "Loops"


def test_lesson_context_is_student_safe():
    class Frappe:
        def get_method(self, dotted, **kwargs):
            return {"message": {
                "name": "LESSON-9", "title": "Loops", "body": "safe", "instructor_notes": "secret",
            }}

    doc = load_lesson_context(Frappe(), {
        "kind": "lesson", "course": "PY-101", "chapter": 2, "lesson_number": 3,
    })
    assert doc == {"name": "LESSON-9", "title": "Loops", "body": "safe"}
