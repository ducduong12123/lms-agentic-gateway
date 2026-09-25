import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.runtime.agent_loop import run_agent_stream
from gateway.runtime.tool_registry import Tool, ToolRegistry
from gateway.tools.envelope import action_result
from gateway.tools.verbs_client import register


class FakeStreamingClient:
    def __init__(self):
        self.calls = 0

    def chat_stream(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            yield {"type": "message", "message": {"content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "navigate", "arguments": '{"route":"/lms/courses/PY-101"}'}}]}}
        else:
            yield {"type": "token", "text": "Đã mở khóa."}
            yield {"type": "message", "message": {"content": "Đã mở khóa.", "tool_calls": []}}



class FakeActionStreamingClient:
    def __init__(self):
        self.calls = 0

    def chat_stream(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            yield {"type": "message", "message": {"content": None, "tool_calls": [{
                "id": "a1", "type": "function",
                "function": {"name": "save_note", "arguments": "{}"},
            }]}}
        else:
            yield {"type": "message", "message": {"content": "Đã xong.", "tool_calls": []}}


class ThoughtStreamingClient:
    def chat_stream(self, messages, tools=None):
        yield {"type": "thought", "text": "RAW_PRIVATE_REASONING"}
        yield {"type": "token", "text": "Câu trả lời."}
        yield {"type": "message", "message": {"content": "Câu trả lời.", "tool_calls": []}}


def test_stream_replaces_raw_reasoning_with_vietnamese_summary():
    events = list(
        run_agent_stream(
            ThoughtStreamingClient(),
            ToolRegistry(),
            "student",
            "xin chào",
            {"user": "student@example.com"},
        )
    )

    assert not any(event["t"] == "thought" for event in events)
    assert any(event["t"] == "plan" for event in events)
    summaries = [event["text"] for event in events if event["t"] == "summary"]
    assert summaries
    assert all("RAW_PRIVATE_REASONING" not in text for text in summaries)
    assert events[-1]["reasoning_summary"]

def test_stream_yields_client_directive_before_done():
    registry = ToolRegistry()
    register(registry)
    events = list(run_agent_stream(FakeStreamingClient(), registry, "student", "mở khóa", {"user": "student@example.com"}))
    kinds = [event["t"] for event in events]
    assert "client" in kinds and kinds.index("client") < kinds.index("done")
    done = events[-1]
    assert done["directives"][0]["op"] == "navigate"


def test_stream_yields_action_card_before_done(monkeypatch):
    registry = ToolRegistry()
    registry.register(Tool(
        "save_note", "test action", {"type": "object"},
        lambda args: action_result("save_note", "Đã làm", "Xong", undo=None),
    ))
    monkeypatch.setattr("gateway.runtime.agent_loop.record_action", lambda **kwargs: None)

    events = list(run_agent_stream(FakeActionStreamingClient(), registry, "student", "làm", {"user": "student@example.com"}))
    kinds = [event["t"] for event in events]

    assert "action" in kinds and kinds.index("action") < kinds.index("done")
    assert events[kinds.index("action")]["card"]["kind"] == "action"


class GroundingStreamingClient:
    def chat_stream(self, messages, tools=None):
        yield {"type": "token", "text": "Đáp án đoán bừa."}
        yield {"type": "message", "message": {"content": "Đáp án đoán bừa.", "tool_calls": []}}


def test_student_course_stream_buffers_until_grounding_gate(monkeypatch):
    monkeypatch.setattr("gateway.runtime.agent_loop.features.create_qa_escalation", lambda **kwargs: {"id": "esc-stream", "status": "open", **kwargs})
    events = list(run_agent_stream(
        GroundingStreamingClient(), ToolRegistry(), "student", "Tại sao đoạn code này lỗi?",
        {"user": "student@example.com", "session_id": "chat-stream",
         "route": {"kind": "lesson", "course": "PY-101", "lesson": "LESSON-1", "path": "/lms/courses/PY-101/learn/1-1"}, "current_lesson": {}},
    ))
    tokens = [event["text"] for event in events if event["t"] == "token"]
    assert tokens == [events[-1]["answer"]]
    assert "Đáp án đoán bừa" not in tokens[0]
    assert "đã được chuyển cho giáo viên" in tokens[0]
    assert events[-1]["escalations"][0]["id"] == "esc-stream"
