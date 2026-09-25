import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.runtime.policy import allowed_tools
from gateway.runtime.tool_bundles import select_bundles
from gateway.tools import copilot_bridge
from gateway.tools.catalog import build_registry

CATALOG = [
    {
        "name": "search_course_content",
        "kind": "read",
        "writes": False,
        "description": "Tìm đoạn bài học để trích dẫn.",
        "parameters": {
            "type": "object",
            "properties": {"course": {"type": "string"}, "query": {"type": "string"}},
            "required": ["course", "query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "escalate_to_teacher",
        "kind": "propose",
        "writes": True,
        "description": "Chuyển câu hỏi cho giáo viên.",
        "parameters": {"type": "object", "properties": {"course": {"type": "string"}, "question": {"type": "string"}}},
    },
]


class FakeFrappe:
    base_url = "http://lms.test"
    site_host = "lms.localhost"
    api_key = ""
    api_secret = ""
    is_configured = True

    def __init__(self, session_id="sid-1", catalog=None):
        self.session_id = session_id
        self.catalog = CATALOG if catalog is None else catalog
        self.calls = []
        self.catalog_requests = 0

    def get_copilot_tools(self):
        self.catalog_requests += 1
        return self.catalog

    def call_copilot_tool(self, tool, arguments=None, **kwargs):
        self.calls.append((tool, arguments, kwargs))
        return {"results": [{"lesson": "L-1", "block_id": "b1"}]}


def setup_function():
    copilot_bridge.clear_cache()


def test_registers_prefixed_tools_and_strips_injected_args():
    frappe = FakeFrappe()
    reg = build_registry(frappe)
    tool = reg.get("copilot_search_course_content")
    assert tool is not None
    assert "copilot.lms" in tool.bundles

    out = tool.func({"course": "PY-101", "query": "try except", "member": "a@x.com", "_role": "student"})
    assert out == {"results": [{"lesson": "L-1", "block_id": "b1"}]}
    name, arguments, kwargs = frappe.calls[0]
    assert name == "search_course_content"
    assert arguments == {"course": "PY-101", "query": "try except"}
    assert "model" in kwargs


def test_write_tools_are_proposals_not_gateway_approvals():
    reg = build_registry(FakeFrappe())
    tool = reg.get("copilot_escalate_to_teacher")
    assert tool.needs_approval is False
    assert tool.risk == "propose"
    assert "chờ giáo viên duyệt" in tool.description


def test_policy_allows_only_registered_copilot_tools():
    reg = build_registry(FakeFrappe(catalog=CATALOG[:1]))
    allowed = allowed_tools("student", reg)
    assert "copilot_search_course_content" in allowed
    assert "copilot_escalate_to_teacher" not in allowed
    assert allowed_tools("student") == allowed_tools("student", None)


def test_catalog_is_cached_per_session():
    frappe = FakeFrappe()
    copilot_bridge.fetch_catalog(frappe)
    copilot_bridge.fetch_catalog(frappe)
    assert frappe.catalog_requests == 1
    other = FakeFrappe(session_id="sid-2")
    copilot_bridge.fetch_catalog(other)
    assert other.catalog_requests == 1


def test_unconfigured_client_registers_nothing():
    frappe = FakeFrappe()
    frappe.is_configured = False
    reg = build_registry(frappe)
    assert not [name for name in reg.names() if name.startswith("copilot_")]


def test_copilot_bundle_only_when_available():
    route = {"kind": "lesson"}
    assert "copilot.lms" not in select_bundles("giải thích bài này", "student", route)
    assert "copilot.lms" in select_bundles("giải thích bài này", "student", route, copilot=True)
    assert "copilot.lms" in select_bundles("tuần này lớp vướng ở đâu", "teacher", {}, copilot=True)
