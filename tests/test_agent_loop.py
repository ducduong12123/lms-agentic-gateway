import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.connector.frappe_client import FrappeClient
from gateway.runtime import agent_loop, features
from gateway.runtime.tool_registry import Tool, ToolRegistry
from gateway.tools.catalog import build_registry


class FakeClient:
    def chat(self, messages, tools=None):
        tool_message = next(message for message in messages if message.get("role") == "tool")
        result = json.loads(tool_message["content"])
        student = result["students"][0]["member"]
        return {
            "choices": [{
                "message": {
                    "content": f"Học viên yếu nhất là {student}; lý do: {result['students'][0]['reasons'][0]}.",
                }
            }]
        }


def test_admin_risk_question_uses_engine_data_without_scope_clarification(monkeypatch):
    monkeypatch.setattr(
        features,
        "risk_overview",
        lambda course, limit: [{
            "member": "weak@test.com",
            "reasons": ["Trượt ít nhất 3 câu quiz"],
            "weakest_p": 0.1,
            "weak_concepts": [],
        }],
    )
    registry = build_registry(FrappeClient("http://localhost"))

    result = agent_loop.run_agent(
        FakeClient(),
        registry,
        "admin",
        "hiện tại học viên yếu nhất của tôi là ai, vì sao",
        {"user": "Administrator", "route": {}},
    )

    assert result["answer"] == "Học viên yếu nhất là weak@test.com; lý do: Trượt ít nhất 3 câu quiz."
    assert result["tool_calls"][0]["tool"] == "list_at_risk_students"
    assert result["tool_calls"][0]["result"]["students"][0]["member"] == "weak@test.com"

class EffortClient:
    def __init__(self):
        self.effort = None

    def chat(self, messages, tools=None, effort="auto"):
        self.effort = effort
        return {"choices": [{"message": {"content": "ok"}}]}


def test_selected_effort_reaches_model_client():
    client = EffortClient()
    agent_loop.run_agent(
        client,
        build_registry(FrappeClient("http://localhost")),
        "student",
        "xin chào",
        {"user": "student@test.com", "route": {}},
        effort="high",
    )
    assert client.effort == "high"


class UnavailableClient:
    def chat(self, messages, tools=None, effort="auto"):
        raise RuntimeError("LLM HTTP 503: usage limit")

    def chat_stream(self, messages, tools=None, effort="auto"):
        raise RuntimeError("LLM HTTP 503: usage limit")


def test_model_failure_returns_usable_nonstream_response():
    result = agent_loop.run_agent(
        UnavailableClient(),
        build_registry(FrappeClient("http://localhost")),
        "student",
        "xin chào",
        {"user": "student@test.com", "route": {}},
    )

    assert "tạm thời hết hạn mức" in result["answer"]


def test_model_failure_returns_done_stream_event():
    events = list(
        agent_loop.run_agent_stream(
            UnavailableClient(),
            build_registry(FrappeClient("http://localhost")),
            "student",
            "xin chào",
            {"user": "student@test.com", "route": {}},
        )
    )

    assert events[-1]["t"] == "done"
    assert "tạm thời hết hạn mức" in events[-1]["answer"]


class UnexpectedClient:
    def chat(self, messages, tools=None, effort="auto"):
        raise AssertionError("durable publish preflight must not ask the model to reconstruct the draft")


def test_save_intent_uses_durable_draft_and_creates_bound_approval(monkeypatch):
    captured = {}

    def request(tool, args, requested_by, requested_role, required_roles):
        captured.update({"tool": tool, "args": dict(args)})
        return "approval-1"

    monkeypatch.setattr(agent_loop, "request_approval", request)
    monkeypatch.setattr(features, "mark_lesson_draft_status", lambda *args, **kwargs: True)
    context = {
        "user": "teacher@test.com",
        "route": {},
        "workflow_state": {
            "active_course": "PY-101",
            "active_draft": {
                "id": "draft-1",
                "course": "PY-101",
                "chapter": "",
                "lesson": "",
                "title": "Bài 2: Điều kiện",
                "body": "# Bài 2\n\nNội dung đầy đủ.",
                "status": "draft",
            },
        },
    }

    result = agent_loop.run_agent(
        UnexpectedClient(),
        build_registry(FrappeClient("http://localhost")),
        "teacher",
        "lưu vào LMS",
        context,
    )

    assert captured["tool"] == "publish_lesson_draft"
    assert captured["args"]["course"] == "PY-101"
    assert captured["args"]["chapter"] == "CH-1"
    assert captured["args"]["draft_id"] == "draft-1"
    assert captured["args"]["body"] == "# Bài 2\n\nNội dung đầy đủ."
    assert result["approvals"] == [{"approval_id": "approval-1", "tool": "publish_lesson_draft"}]
    assert result["actions"][0]["approval_id"] == "approval-1"


def test_approval_modes_gate_only_the_requested_risk(monkeypatch):
    approvals = []
    executions = []
    monkeypatch.setattr(
        agent_loop,
        "request_approval",
        lambda tool, args, **kwargs: approvals.append((tool, dict(args))) or f"approval-{len(approvals)}",
    )
    registry = ToolRegistry()
    registry.register(Tool(
        "manage_course",
        "mutate",
        {"type": "object"},
        lambda args: executions.append(dict(args)) or {"ok": True},
        needs_approval=True,
        approval_roles={"teacher", "admin"},
    ))
    allowed = ["manage_course"]

    ask_result, ask_approval = agent_loop._run_tool(
        registry, allowed, "teacher", "manage_course",
        {"operation": "update", "course": "C-1"},
        {"user": "teacher@test.com", "approval_mode": "ask"},
    )
    auto_result, auto_approval = agent_loop._run_tool(
        registry, allowed, "teacher", "manage_course",
        {"operation": "update", "course": "C-1"},
        {"user": "teacher@test.com", "approval_mode": "auto"},
    )
    unsafe_result, unsafe_approval = agent_loop._run_tool(
        registry, allowed, "teacher", "manage_course",
        {"operation": "delete", "course": "C-1"},
        {"user": "teacher@test.com", "approval_mode": "auto"},
    )
    full_result, full_approval = agent_loop._run_tool(
        registry, allowed, "teacher", "manage_course",
        {"operation": "delete", "course": "C-1"},
        {"user": "teacher@test.com", "approval_mode": "full_access"},
    )
    registry.get("manage_course").approval_roles = {"admin"}
    elevated_result, elevated_approval = agent_loop._run_tool(
        registry, allowed, "teacher", "manage_course",
        {"operation": "update", "course": "C-1"},
        {"user": "teacher@test.com", "approval_mode": "full_access"},
    )

    assert ask_result["status"] == unsafe_result["status"] == elevated_result["status"] == "pending_approval"
    assert ask_approval["approval_id"] == "approval-1"
    assert unsafe_approval["approval_id"] == "approval-2"
    assert elevated_approval["approval_id"] == "approval-3"
    assert auto_result == full_result == {"ok": True}
    assert auto_approval is full_approval is None
    assert [item["operation"] for item in executions] == ["update", "delete"]
