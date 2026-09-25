"""F2: trợ giảng học viên dựa trên nội dung khóa học của lms_copilot.

Khi site có cài lms_copilot và học viên thấy tool ``copilot_search_course_content``, agent trả lời
từ các khối bài học có thể trích dẫn. Sau mỗi câu trả lời, runtime (không phải LLM) kiểm tra trích
dẫn, ghi lượt hỏi đáp bằng ``copilot_log_conversation_turn`` và giữ id Copilot Conversation theo
chat session của Gateway, để lượt sau và ``escalate_to_teacher`` gắn đúng hội thoại.

Không có lms_copilot thì ``is_active`` trả False và agent chạy như cũ.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import unicodedata
from pathlib import Path

from ..tools.envelope import validate_route

DB = Path(__file__).resolve().parents[3] / "data" / "gateway.db"

SEARCH_TOOL = "copilot_search_course_content"
LESSON_TOOL = "copilot_get_lesson_content"
OUTLINE_TOOL = "copilot_get_course_outline"
LOG_TOOL = "copilot_log_conversation_turn"
ESCALATE_TOOL = "copilot_escalate_to_teacher"
# Tool chỉ runtime được gọi; LLM không thấy (xem policy.RUNTIME_ONLY).
MAX_CITATIONS = 8

CITE_RE = re.compile(r"\[\[\s*cite\s*:\s*([^\]#\s]+)\s*#\s*([^\]\s]+)\s*\]\]", re.IGNORECASE)

NO_GROUNDING_NOTE = (
    "Mình chưa tìm thấy đoạn nào trong bài học của khóa để làm căn cứ cho câu trả lời này. "
    "Bạn có muốn chuyển câu hỏi cho giáo viên không? Bấm “Hỏi giáo viên” bên dưới."
)
OFFSCOPE_NOTE = (
    "Câu hỏi này thuộc phạm vi giáo viên phụ trách. Bấm “Hỏi giáo viên” bên dưới để chuyển câu hỏi nhé."
)

_OFFSCOPE_PHRASES = (
    "hoc phi", "lich hoc", "lich thi", "khieu nai", "xin dap an", "cho dap an", "cho xin dap an",
    "dap an bai tap", "dap an assignment", "hoan tien", "thanh toan", "gia han nop", "gia han deadline",
)


# ------------------------------------------------------------------ storage


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS copilot_conversation ("
        "member TEXT NOT NULL, session_id TEXT NOT NULL, conversation TEXT NOT NULL, "
        "course TEXT NOT NULL DEFAULT '', updated REAL NOT NULL, "
        "PRIMARY KEY(member, session_id))"
    )
    return conn


def get_conversation(member: str, session_id: str) -> str:
    member, session_id = str(member or ""), str(session_id or "")
    if not member or not session_id:
        return ""
    with _conn() as conn:
        row = conn.execute(
            "SELECT conversation FROM copilot_conversation WHERE member=? AND session_id=?",
            (member, session_id),
        ).fetchone()
    return str(row["conversation"]) if row else ""


def set_conversation(member: str, session_id: str, conversation: str, course: str = "") -> None:
    member, session_id, conversation = str(member or ""), str(session_id or ""), str(conversation or "")
    if not member or not session_id or not conversation:
        return
    with _conn() as conn:
        conn.execute(
            "INSERT INTO copilot_conversation(member, session_id, conversation, course, updated) "
            "VALUES (?,?,?,?,?) ON CONFLICT(member, session_id) DO UPDATE SET "
            "conversation=excluded.conversation, course=excluded.course, updated=excluded.updated",
            (member, session_id, conversation, str(course or ""), time.time()),
        )


def forget_member(member: str) -> int:
    with _conn() as conn:
        return conn.execute("DELETE FROM copilot_conversation WHERE member=?", (str(member or ""),)).rowcount


# ------------------------------------------------------------------ prompt


def _plain(value: str) -> str:
    text = unicodedata.normalize("NFD", str(value or "")).encode("ascii", "ignore").decode().casefold()
    return " ".join(text.split())


def is_active(role: str, registry) -> bool:
    """Chế độ trợ giảng F2: học viên + lms_copilot có tool tìm nội dung khóa học."""
    return role == "student" and registry is not None and registry.get(SEARCH_TOOL) is not None


def is_offscope(message: str) -> bool:
    text = _plain(message)
    return any(phrase in text for phrase in _OFFSCOPE_PHRASES)


def prompt_block(context: dict) -> str:
    route = context.get("route") if isinstance(context.get("route"), dict) else {}
    course = str(route.get("course") or "")
    lesson = str(route.get("lesson") or "")
    lines = [
        "Chế độ trợ giảng khóa học (lms_copilot). Chỉ trả lời từ nội dung khóa học đã duyệt:",
        f"- Gọi {SEARCH_TOOL} (course, query) hoặc {LESSON_TOOL} (lesson) trước khi giải thích; "
        "không dùng kiến thức ngoài khóa học làm căn cứ.",
        "- Mỗi ý lấy từ bài học phải kèm dấu trích dẫn đúng dạng [[cite:<lesson>#<block_id>]], "
        "với lesson và block_id lấy nguyên văn từ kết quả tool (trường `cite`). Ít nhất 1 trích dẫn. "
        "Dấu này thay cho nguồn dạng `LMS Course: …, Course Lesson: …`; Gateway sẽ đổi thành link tới bài học.",
        "- Nếu tool không trả về đoạn nào liên quan: nói rõ là chưa tìm thấy căn cứ trong khóa học "
        "và đề nghị chuyển câu hỏi cho giáo viên; không tự bịa câu trả lời.",
        "- Câu hỏi về học phí, lịch học, lịch thi, hạn nộp, khiếu nại hoặc xin đáp án là ngoài phạm vi: "
        f"không trả lời nội dung, đề nghị chuyển cho giáo viên; chỉ gọi {ESCALATE_TOOL} khi học viên đồng ý.",
    ]
    if course:
        lines.append(f"- Khóa học hiện tại: course={course}" + (f"; bài đang mở: lesson={lesson}." if lesson else "."))
    pending = [str(item) for item in context.get("pending_assignments") or [] if item]
    if pending:
        lines.append(
            "- Bài học này có bài tập chưa có kết quả (" + ", ".join(pending[:5]) + "). "
            "CHỈ gợi ý hướng làm, câu hỏi dẫn dắt và khái niệm liên quan; không đưa lời giải, "
            "code hoàn chỉnh hay đáp án cuối cùng."
        )
    if context.get("_offscope"):
        lines.append("- Câu hỏi vừa rồi có vẻ ngoài phạm vi nội dung khóa học: hãy đề nghị chuyển cho giáo viên.")
    return "\n".join(lines)


def lesson_pointer(context: dict) -> str:
    """Thay cho việc bơm cả nội dung bài vào prompt: buộc agent đọc qua tool để có block_id."""
    lesson = context.get("current_lesson") or {}
    if not isinstance(lesson, dict) or not lesson.get("name"):
        return ""
    return (
        f"Bài đang mở: {lesson.get('title') or lesson.get('name')} (lesson={lesson.get('name')}). "
        f"Đọc nội dung bằng {LESSON_TOOL} để có block_id trích dẫn."
    )


# ------------------------------------------------------------------ pending assignments

_ASSIGNMENT_MACRO = re.compile(r"Assignment\(\s*[\"']([^\"']+)[\"']")
GRADED = {"Pass", "Fail"}


def lesson_assignments(lesson: dict) -> list[str]:
    """Assignment nhúng trong bài: khối EditorJS ``assignment`` hoặc macro trong body."""
    if not isinstance(lesson, dict):
        return []
    found: list[str] = []
    content = lesson.get("content")
    try:
        data = json.loads(content) if isinstance(content, str) and content.strip() else content
    except (TypeError, ValueError):
        data = None
    for block in (data or {}).get("blocks") or [] if isinstance(data, dict) else []:
        if isinstance(block, dict) and block.get("type") == "assignment":
            name = str((block.get("data") or {}).get("assignment") or "").strip()
            if name:
                found.append(name)
    found.extend(_ASSIGNMENT_MACRO.findall(str(lesson.get("body") or "")))
    return list(dict.fromkeys(found))


def pending_assignments(frappe, member: str, lesson: dict) -> list[str]:
    """Assignment của bài mà học viên chưa có kết quả chấm (Pass/Fail)."""
    pending = []
    for assignment in lesson_assignments(lesson):
        try:
            rows = frappe.list_documents(
                "LMS Assignment Submission",
                filters={"assignment": assignment, "member": member},
                fields=["name", "status"], limit=5,
            ).get("data") or []
        except Exception:
            rows = []
        if not any(str(row.get("status") or "") in GRADED for row in rows if isinstance(row, dict)):
            pending.append(assignment)
    return pending


# ------------------------------------------------------------------ citations


def annotate_citable(tool_name: str, out) -> None:
    """Thêm trường ``cite`` vào kết quả tool để LLM chép đúng dấu trích dẫn."""
    if not isinstance(out, dict):
        return
    if tool_name == SEARCH_TOOL:
        for item in out.get("results") or []:
            if isinstance(item, dict) and item.get("lesson") and item.get("block_id"):
                item["cite"] = f"[[cite:{item['lesson']}#{item['block_id']}]]"
    elif tool_name == LESSON_TOOL:
        lesson = out.get("lesson")
        for section in out.get("sections") or []:
            if lesson and isinstance(section, dict) and section.get("block_id"):
                section["cite"] = f"[[cite:{lesson}#{section['block_id']}]]"


def _retrieved_blocks(tool_calls: list[dict]) -> tuple[dict, str]:
    """Các khối đã thực sự đọc qua tool trong lượt này: (lesson, block_id) -> thông tin."""
    blocks: dict[tuple[str, str], dict] = {}
    course = ""
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        result = call.get("result")
        if not isinstance(result, dict) or result.get("error"):
            continue
        if call.get("tool") == SEARCH_TOOL:
            course = course or str((call.get("args") or {}).get("course") or "")
            for item in result.get("results") or []:
                if isinstance(item, dict) and item.get("lesson") and item.get("block_id"):
                    key = (str(item["lesson"]), str(item["block_id"]))
                    blocks.setdefault(key, {
                        "label": str(item.get("citation") or item.get("lesson_title") or item["lesson"]),
                        "score": float(item.get("score") or 0),
                    })
        elif call.get("tool") == LESSON_TOOL:
            course = course or str(result.get("course") or "")
            lesson = str(result.get("lesson") or "")
            title = str(result.get("title") or lesson)
            for section in result.get("sections") or []:
                if lesson and isinstance(section, dict) and section.get("block_id"):
                    heading = str(section.get("heading") or "")
                    blocks.setdefault((lesson, str(section["block_id"])), {
                        "label": title + (f" · {heading}" if heading else ""),
                    })
    return blocks, course


def extract_citations(answer: str, tool_calls: list[dict]) -> tuple[str, list[dict], str]:
    """Đổi dấu [[cite:…]] thành [n]; chỉ giữ khối đã đọc qua tool trong lượt này.

    Trả về (answer đã làm sạch, citations, course suy ra từ tool).
    """
    blocks, course = _retrieved_blocks(tool_calls)
    citations: list[dict] = []
    index: dict[tuple[str, str], int] = {}

    def replace(match: re.Match) -> str:
        key = (match.group(1), match.group(2))
        if key not in blocks:
            return ""
        if key not in index:
            if len(citations) >= MAX_CITATIONS:
                return ""
            citations.append({"lesson": key[0], "block_id": key[1], "label": blocks[key]["label"][:200]})
            index[key] = len(citations)
        return f"[{index[key]}]"

    cleaned = CITE_RE.sub(replace, str(answer or ""))
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned).strip()
    return cleaned, citations, course


# Điểm tối thiểu (tỉ lệ từ khóa trùng) để một khối tìm được coi là "bài học liên quan".
RELATED_MIN_SCORE = 0.5
MAX_RELATED = 2
_MODEL_SOURCE_LINE = re.compile(r"^[^\n]*(?:Nguồn|nguồn)[^\n]*(?:LMS Course|Course Lesson)[^\n]*$\n?", re.M)


def related_citations(tool_calls: list[dict]) -> list[dict]:
    """Model không tự gắn [[cite:…]]: lấy các khối Gateway đã tìm có điểm đủ cao làm bài học liên quan."""
    blocks, _course = _retrieved_blocks(tool_calls)
    ranked = sorted(
        ((info.get("score", 0), key, info) for key, info in blocks.items() if info.get("score", 0) >= RELATED_MIN_SCORE),
        key=lambda item: item[0],
        reverse=True,
    )
    return [
        {"lesson": key[0], "block_id": key[1], "label": info["label"][:200], "related": True}
        for _score, key, info in ranked[:MAX_RELATED]
    ]


def lesson_routes(registry, course: str) -> dict[str, str]:
    """lesson -> route SPA ``/lms/courses/<course>/learn/<chương>-<bài>`` từ outline lms_copilot."""
    tool = registry.get(OUTLINE_TOOL) if registry is not None and course else None
    if tool is None:
        return {}
    try:
        outline = tool.func({"course": course})
    except Exception:
        return {}
    routes: dict[str, str] = {}
    for chapter in (outline or {}).get("chapters") or [] if isinstance(outline, dict) else []:
        for lesson in chapter.get("lessons") or [] if isinstance(chapter, dict) else []:
            number = str((lesson or {}).get("number") or "") if isinstance(lesson, dict) else ""
            chapter_no, _, lesson_no = number.partition(".")
            if not (chapter_no.isdigit() and lesson_no.isdigit()):
                continue
            try:
                routes[str(lesson["lesson"])] = validate_route(
                    f"/lms/courses/{course}/learn/{int(chapter_no)}-{int(lesson_no)}"
                )
            except (KeyError, ValueError):
                continue
    return routes


# ------------------------------------------------------------------ finish a turn


def _call_log(registry, args: dict, conversation: str, usage: dict, model: str):
    tool = registry.get(LOG_TOOL)
    meta = {
        "_conversation": conversation,
        "_model": model,
        "_tokens_in": usage.get("tokens_in"),
        "_tokens_out": usage.get("tokens_out"),
    }
    return tool.func({**args, **{key: value for key, value in meta.items() if value}})


def finish_turn(registry, role: str, user_msg: str, answer: str, tool_calls: list[dict],
                context: dict, model: str = "") -> tuple[str, dict | None]:
    """Kiểm tra trích dẫn, ghi lượt vào lms_copilot một lần, trả (answer, thẻ tutor)."""
    if not is_active(role, registry):
        return answer, None
    cleaned, citations, tool_course = extract_citations(answer, tool_calls)
    # Dòng "Nguồn: LMS Course …, Course Lesson …" model tự viết thay bằng trích dẫn thật của Gateway.
    cleaned = _MODEL_SOURCE_LINE.sub("", cleaned).strip()
    offscope = bool(context.get("_offscope"))
    if not citations and not offscope:
        citations = related_citations(tool_calls)
        if citations:
            cleaned += "\n\nBài học liên quan: " + "; ".join(
                f"[{index}] {item['label']}" for index, item in enumerate(citations, 1)
            )
    escalated = any(
        isinstance(call, dict) and call.get("tool") == ESCALATE_TOOL
        and isinstance(call.get("result"), dict) and not call["result"].get("error")
        for call in tool_calls or []
    )
    if not citations and not escalated:
        note = OFFSCOPE_NOTE if offscope else NO_GROUNDING_NOTE
        if note not in cleaned:
            cleaned = (cleaned + "\n\n" + note).strip() if cleaned else note

    route = context.get("route") if isinstance(context.get("route"), dict) else {}
    course = str(route.get("course") or tool_course or "")
    lesson = str(route.get("lesson") or "")
    member = str(context.get("user") or "")
    session_id = str(context.get("session_id") or "")
    card = {
        "kind": "tutor",
        "course": course,
        "lesson": lesson,
        "question": str(user_msg or "")[:4000],
        "citations": [],
        "grounded": bool(citations),
        "escalated": escalated,
        "suggest_escalation": not citations and not escalated,
        "conversation": "",
        "message_index": 0,
    }
    routes = lesson_routes(registry, course) if citations else {}
    card["citations"] = [{**item, "route": routes.get(item["lesson"], "")} for item in citations]

    if course and registry.get(LOG_TOOL) is not None:
        args = {
            "course": course,
            "question": str(user_msg or "")[:4000],
            "answer": cleaned[:8000],
            "citations": citations,
        }
        if lesson:
            args["lesson"] = lesson
        usage = context.get("_usage") if isinstance(context.get("_usage"), dict) else {}
        conversation = get_conversation(member, session_id)
        result = _call_log(registry, args, conversation, usage, model)
        if conversation and isinstance(result, dict) and result.get("error"):
            # Hội thoại cũ đã bị xoá (hết hạn lưu) hoặc không còn thuộc user: mở hội thoại mới.
            result = _call_log(registry, args, "", usage, model)
        if isinstance(result, dict) and result.get("conversation"):
            card["conversation"] = str(result["conversation"])
            card["message_index"] = int(result.get("message_index") or 0)
            set_conversation(member, session_id, card["conversation"], course)
    return cleaned, card
