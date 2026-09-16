import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.tools.envelope import action_result, client_directive, validate_view_spec


def test_action_result_has_closed_contract_and_undo_window():
    result = action_result(
        "enroll_course", "Đã ghi danh", "Đã ghi danh PY-101.",
        changes=[{"doctype": "LMS Enrollment", "name": "ENR-1", "op": "create"}],
        undo={"op": "delete", "doctype": "LMS Enrollment", "name": "ENR-1"},
        view={"type": "course", "course": "PY-101"},
    )
    assert result["kind"] == "action"
    assert result["status"] == "done"
    assert result["undo"]["available"] is True
    assert result["view"] == {"type": "course", "course": "PY-101"}


def test_view_spec_rejects_unknown_type_and_client_directive_validates():
    with pytest.raises(ValueError, match="unsupported view"):
        validate_view_spec({"type": "html", "value": "<script>"})
    directive = client_directive("render_view", spec={"type": "lesson", "course": "PY-101", "chapter": 1, "lesson_number": 2})
    assert directive == {"kind": "client", "op": "render_view", "spec": {"type": "lesson", "course": "PY-101", "chapter": 1, "lesson_number": 2}}
    with pytest.raises(ValueError, match="local LMS"):
        client_directive("navigate", route="https://evil.example")
