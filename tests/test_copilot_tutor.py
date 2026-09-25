import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.runtime import agent_loop, approval, learner, tutor
from gateway.tools import copilot_bridge
from gateway.tools.catalog import build_registry


def _tool(name, properties, writes=False):
    return {
        "name": name,
        "kind": "propose" if writes else "read",
        "writes": writes,
        "description": name,
        "parameters": {"type": "object", "properties": {key: {"type": "string"} for key in properties}},
    }


LEARNER_CATALOG = [
    _tool("get_course_outline", ["course"]),
    _tool("get_lesson_content", ["lesson"]),
    _tool("search_course_content", ["course", "query", "limit"]),
    _tool("log_conversation_turn", ["course", "lesson", "question", "answer", "citations", "model"], writes=True),
    _tool("escalate_to_teacher", ["course", "lesson", "question", "summary"], writes=True),
]


class FakeFrappe:
    base_url = "http://lms.test"
    site_host = "lms.localhost"
    api_key = ""
    api_secret = ""
    is_configured = True

    def __init__(self, catalog=None, session_id="sid-learner"):
        self.session_id = session_id
        self.catalog = LEARNER_CATALOG if catalog is None else catalog
        self.calls = []
        self.turns = 0
        self.search_results = None

    def get_copilot_tools(self):
        return self.catalog

    def call_copilot_tool(self, tool, arguments=None, **kwargs):
        self.calls.append((tool, dict(arguments or {}), kwargs))
        if tool == "search_course_content" and self.search_results is not None:
            return {"query": arguments["query"], "results": self.search_results}
        if tool == "search_course_content":
            return {"query": arguments["query"], "results": [{
                "lesson": "LS-1", "lesson_title": "Vòng lặp", "block_id": "b1", "heading": "For",
                "snippet": "for i in range(3)", "citation": "Vòng lặp · For", "score": 1.0,
            }]}
        if tool == "get_course_outline":
            return {"course": "PY-101", "chapters": [
                {"chapter": "CH-1", "number": 1, "lessons": [{"lesson": "LS-1", "title": "Vòng lặp", "number": "1.2"}]},
            ]}
        if tool == "log_conversation_turn":
            self.turns += 1
            return {"conversation": "CPC-1", "message_index": self.turns * 2}
        if tool == "escalate_to_teacher":
            return {"proposal": "CPP-1", "status": "Pending"}
        return {}

    def logged(self):
        return [call for call in self.calls if call[0] == "log_conversation_turn"]


class ScriptedLLM:
    model = "fake-model"

    def __init__(self, *messages):
        self.script = list(messages)
        self.seen_tools = []
        self.seen_messages = []

    def _next(self, messages, tools):
        self.seen_tools.append([tool["function"]["name"] for tool in tools or []])
        self.seen_messages.append(messages)
        message = self.script.pop(0)
        return {"choices": [{"message": message}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}

    def chat(self, messages, tools=None):
        return self._next(messages, tools)

    def chat_stream(self, messages, tools=None):
        res = self._next(messages, tools)
        message = res["choices"][0]["message"]
        if message.get("content"):
            yield {"type": "token", "text": message["content"]}
        yield {"type": "message", "message": message, "usage": res["usage"]}


def _call(name, **args):
    return {"content": None, "tool_calls": [{
        "id": "c-" + name, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }]}


def _search_then(answer):
    return ScriptedLLM(
        _call("copilot_search_course_content", course="PY-101", query="vòng lặp for"),
        {"content": answer},
    )


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    copilot_bridge.clear_cache()
    monkeypatch.setattr(tutor, "DB", tmp_path / "tutor.db")
    monkeypatch.setattr(learner, "DB", tmp_path / "learner.db")
    monkeypatch.setattr(approval, "DB", tmp_path / "approval.db")


def _context(session="cht_1"):
    return {
        "user": "hv@test.com",
        "session_id": session,
        "route": {"kind": "lesson", "course": "PY-101", "lesson": "LS-1", "lesson_title": "Vòng lặp"},
        "current_lesson": {"name": "LS-1", "title": "Vòng lặp", "body": "NỘI DUNG ĐẦY ĐỦ"},
    }


def test_answer_citations_are_checked_and_rendered_as_lesson_links():
    frappe = FakeFrappe()
    registry = build_registry(frappe)
    llm = _search_then("Vòng for lặp qua dãy [[cite:LS-1#b1]]. Bịa thêm [[cite:LS-9#zz]].")

    result = agent_loop.run_agent(llm, registry, "student", "vòng lặp for là gì?", _context())

    assert "[[cite" not in result["answer"]
    assert "[1]" in result["answer"]
    card = result["tutor"]
    assert card["grounded"] is True
    assert card["citations"] == [{
        "lesson": "LS-1", "block_id": "b1", "label": "Vòng lặp · For",
        "route": "/lms/courses/PY-101/learn/1-2",
    }]
    # Nội dung bài không bị bơm nguyên văn vào prompt; agent phải đọc qua tool để có block_id.
    prompt = " ".join(str(m.get("content")) for m in llm.seen_messages[0] if m["role"] == "system")
    assert "NỘI DUNG ĐẦY ĐỦ" not in prompt
    assert "[[cite:" in prompt
    # get_lesson_context cũ và tool ghi log không được đưa cho LLM.
    assert "get_lesson_context" not in llm.seen_tools[0]
    assert "copilot_log_conversation_turn" not in llm.seen_tools[0]
    assert "copilot_search_course_content" in llm.seen_tools[0]


def test_every_answer_is_logged_once_with_citations_model_and_tokens():
    frappe = FakeFrappe()
    registry = build_registry(frappe)
    agent_loop.run_agent(
        _search_then("Xem [[cite:LS-1#b1]]."), registry, "student", "vòng lặp for?", _context(),
    )

    logged = frappe.logged()
    assert len(logged) == 1
    _, arguments, kwargs = logged[0]
    assert arguments["course"] == "PY-101"
    assert arguments["lesson"] == "LS-1"
    assert arguments["question"] == "vòng lặp for?"
    assert arguments["citations"] == [{"lesson": "LS-1", "block_id": "b1", "label": "Vòng lặp · For"}]
    assert "[[cite" not in arguments["answer"]
    assert kwargs["model"] == "fake-model"
    assert kwargs["tokens_in"] == 200 and kwargs["tokens_out"] == 40
    assert kwargs["conversation"] is None


def test_conversation_id_is_kept_per_session_and_reused():
    frappe = FakeFrappe()
    registry = build_registry(frappe)
    first = agent_loop.run_agent(_search_then("A [[cite:LS-1#b1]]"), registry, "student", "hỏi 1", _context())
    assert first["tutor"]["conversation"] == "CPC-1"
    assert tutor.get_conversation("hv@test.com", "cht_1") == "CPC-1"

    agent_loop.run_agent(_search_then("B [[cite:LS-1#b1]]"), registry, "student", "hỏi 2", _context())
    assert [call[2]["conversation"] for call in frappe.logged()] == [None, "CPC-1"]

    # Chat session khác của Gateway mở Copilot Conversation mới.
    agent_loop.run_agent(_search_then("C [[cite:LS-1#b1]]"), registry, "student", "hỏi 3", _context("cht_2"))
    assert frappe.logged()[-1][2]["conversation"] is None


def test_escalation_by_the_agent_reuses_the_conversation():
    frappe = FakeFrappe()
    registry = build_registry(frappe)
    tutor.set_conversation("hv@test.com", "cht_1", "CPC-7")
    llm = ScriptedLLM(
        _call("copilot_escalate_to_teacher", course="PY-101", question="Học phí bao nhiêu?"),
        {"content": "Mình đã chuyển câu hỏi cho giáo viên."},
    )
    result = agent_loop.run_agent(llm, registry, "student", "Học phí bao nhiêu?", _context())

    escalations = [call for call in frappe.calls if call[0] == "escalate_to_teacher"]
    assert escalations[0][2]["conversation"] == "CPC-7"
    assert result["tutor"]["escalated"] is True
    assert result["tutor"]["suggest_escalation"] is False
    assert tutor.NO_GROUNDING_NOTE not in result["answer"]


def test_answer_without_grounding_offers_the_teacher():
    frappe = FakeFrappe()
    frappe.search_results = []
    registry = build_registry(frappe)
    result = agent_loop.run_agent(
        ScriptedLLM({"content": "Python ra đời năm 1991."}), registry, "student", "Python ra đời khi nào?", _context(),
    )
    assert result["answer"].startswith("Python ra đời năm 1991.")
    assert tutor.NO_GROUNDING_NOTE in result["answer"]
    assert result["tutor"]["grounded"] is False
    assert result["tutor"]["suggest_escalation"] is True
    assert frappe.logged()[0][1]["citations"] == []


def test_offscope_question_proposes_teacher():
    frappe = FakeFrappe()
    registry = build_registry(frappe)
    llm = ScriptedLLM({"content": "Mình không có thông tin này."})
    result = agent_loop.run_agent(llm, registry, "student", "Học phí khóa này bao nhiêu?", _context())
    assert tutor.OFFSCOPE_NOTE in result["answer"]
    prompt = " ".join(str(m.get("content")) for m in llm.seen_messages[0] if m["role"] == "system")
    assert "ngoài phạm vi" in prompt


def test_stream_logs_once_and_sends_tutor_card():
    frappe = FakeFrappe()
    registry = build_registry(frappe)
    events = list(agent_loop.run_agent_stream(
        _search_then("Stream [[cite:LS-1#b1]]"), registry, "student", "vòng lặp?", _context(),
    ))
    done = events[-1]
    assert done["t"] == "done"
    assert done["answer"] == "Stream [1]"
    assert done["tutor"]["citations"][0]["route"] == "/lms/courses/PY-101/learn/1-2"
    assert len(frappe.logged()) == 1
    assert frappe.logged()[0][2]["tokens_in"] == 200


def test_without_lms_copilot_behaviour_is_unchanged():
    frappe = FakeFrappe(catalog=[])
    registry = build_registry(frappe)
    llm = ScriptedLLM({"content": "Trả lời cũ [[cite:LS-1#b1]]"})
    result = agent_loop.run_agent(llm, registry, "student", "vòng lặp?", _context())
    assert result["answer"] == "Trả lời cũ [[cite:LS-1#b1]]"
    assert "tutor" not in result
    assert frappe.calls == []
    prompt = " ".join(str(m.get("content")) for m in llm.seen_messages[0] if m["role"] == "system")
    assert "NỘI DUNG ĐẦY ĐỦ" in prompt


def test_teacher_turns_are_not_logged():
    frappe = FakeFrappe()
    registry = build_registry(frappe)
    result = agent_loop.run_agent(ScriptedLLM({"content": "ok"}), registry, "teacher", "chào", _context())
    assert "tutor" not in result
    assert frappe.logged() == []


class AssignmentFrappe:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def list_documents(self, doctype, filters=None, fields=None, limit=20):
        self.queries.append((doctype, filters))
        return {"data": self.rows.get(filters["assignment"], [])}


def test_pending_assignment_switches_to_hints_only():
    lesson = {
        "name": "LS-1",
        "content": json.dumps({"blocks": [
            {"id": "a", "type": "paragraph", "data": {"text": "x"}},
            {"id": "b", "type": "assignment", "data": {"assignment": "ASG-1"}},
            {"id": "c", "type": "assignment", "data": {"assignment": "ASG-2"}},
        ]}),
    }
    frappe = AssignmentFrappe({"ASG-2": [{"name": "S-1", "status": "Pass"}]})
    pending = tutor.pending_assignments(frappe, "hv@test.com", lesson)
    assert pending == ["ASG-1"]
    assert frappe.queries[0] == ("LMS Assignment Submission", {"assignment": "ASG-1", "member": "hv@test.com"})

    block = tutor.prompt_block({**_context(), "pending_assignments": pending})
    assert "CHỈ gợi ý" in block and "ASG-1" in block
    assert "CHỈ gợi ý" not in tutor.prompt_block(_context())


def test_gateway_retrieves_course_content_even_if_llm_skips_the_tool():
    frappe = FakeFrappe()
    registry = build_registry(frappe)
    # Model trả lời ngay, không gọi tool nào: Gateway vẫn phải tìm trước và cho phép trích dẫn.
    llm = ScriptedLLM({"content": "Dùng vòng for [[cite:LS-1#b1]]."})
    result = agent_loop.run_agent(llm, registry, "student", "vòng lặp for?", _context())

    searches = [call for call in frappe.calls if call[0] == "search_course_content"]
    assert searches and searches[0][1] == {"course": "PY-101", "query": "vòng lặp for?"}
    prompt = " ".join(str(m.get("content")) for m in llm.seen_messages[0] if m["role"] == "system")
    assert "Gateway đã gọi copilot_search_course_content" in prompt and '"b1"' in prompt
    roles = [(m["role"], str(m.get("content"))) for m in llm.seen_messages[0]]
    prefetch_at = next(i for i, (_, text) in enumerate(roles) if text.startswith("Gateway đã gọi"))
    assert roles[prefetch_at + 1] == ("user", "vòng lặp for?")
    assert result["tutor"]["grounded"] is True
    assert result["tutor"]["citations"][0]["block_id"] == "b1"


def test_offscope_question_skips_retrieval():
    frappe = FakeFrappe()
    registry = build_registry(frappe)
    agent_loop.run_agent(ScriptedLLM({"content": "Hỏi giáo viên nhé."}), registry, "student",
                         "Học phí khóa này bao nhiêu?", _context())
    assert not [call for call in frappe.calls if call[0] == "search_course_content"]


def test_related_lessons_are_attached_when_model_skips_cite_markers():
    frappe = FakeFrappe()
    registry = build_registry(frappe)
    answer = "Dùng vòng for để lặp.\nNguồn: LMS Course: chưa xác định, Course Lesson: chưa xác định"
    result = agent_loop.run_agent(ScriptedLLM({"content": answer}), registry, "student", "vòng lặp for?", _context())
    assert "LMS Course" not in result["answer"]
    assert "Bài học liên quan: [1] Vòng lặp · For" in result["answer"]
    assert tutor.NO_GROUNDING_NOTE not in result["answer"]
    assert result["tutor"]["citations"][0]["related"] is True
    assert result["tutor"]["citations"][0]["route"] == "/lms/courses/PY-101/learn/1-2"


def test_weak_matches_are_not_shown_as_related():
    frappe = FakeFrappe()
    frappe.search_results = [{"lesson": "LS-1", "block_id": "b1", "citation": "Vòng lặp", "score": 0.2}]
    registry = build_registry(frappe)
    result = agent_loop.run_agent(ScriptedLLM({"content": "Không rõ."}), registry, "student", "abc xyz?", _context())
    assert result["tutor"]["citations"] == []
    assert tutor.NO_GROUNDING_NOTE in result["answer"]


def test_search_courses_is_hidden_when_course_is_known():
    registry = build_registry(FakeFrappe())
    llm = ScriptedLLM({"content": "Xem [[cite:LS-1#b1]]."})
    agent_loop.run_agent(llm, registry, "student", "vòng lặp for?", _context())
    assert "search_courses" not in llm.seen_tools[0]


def test_related_lessons_keep_only_blocks_close_to_the_best():
    calls = [{"tool": tutor.SEARCH_TOOL, "args": {"course": "PY-101"}, "result": {"results": [
        {"lesson": "L3", "block_id": "try", "citation": "try/except", "score": 0.56},
        {"lesson": "L2", "block_id": "loop", "citation": "Vòng lặp", "score": 0.44},
        {"lesson": "L3", "block_id": "err", "citation": "Lỗi", "score": 0.5},
    ]}}]
    assert [item["block_id"] for item in tutor.related_citations(calls)] == ["try", "err"]
