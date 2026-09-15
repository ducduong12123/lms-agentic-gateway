"""Vong lap agent don: chat -> tool -> chat, toi da 6 vong. Ho tro stream SSE + timing."""
from __future__ import annotations

import json
import time

from .approval import audit, request_approval
from .learner import concepts_for_lesson, format_learner_block, weak_concepts
from .long_memory import format_memories_block
from .policy import allowed_tools
from .trace import trace as _trace


def _memory_block(context: dict) -> str:
    memories = context.get("long_term_memories") or []
    if not isinstance(memories, list) or not memories:
        return ""
    return format_memories_block(memories)


def _learner_block(context: dict) -> str:
    states = context.get("learner_states") or []
    if not isinstance(states, list):
        return ""
    weak = [item for item in states if isinstance(item, dict)]
    lesson = context.get("lesson_concepts") or []
    if not weak and not lesson:
        return ""
    return format_learner_block(weak, lesson if isinstance(lesson, list) else None)


def _route_block(context: dict) -> str:
    route = context.get("route") or {}
    if not isinstance(route, dict) or route.get("kind") not in {"course", "lesson"}:
        return ""
    course = str(route.get("course") or "")
    lesson = str(route.get("lesson") or "")
    title = str(route.get("lesson_title") or "")
    if lesson:
        return (
            f"Ngữ cảnh trang LMS hiện tại: course={course}; lesson={lesson}"
            + (f" ({title})" if title else "")
            + ". Khi người dùng nói ‘bài này’, hãy dùng đúng lesson này và gọi get_lesson_context nếu cần nội dung."
        )
    return f"Ngữ cảnh trang LMS hiện tại: course={course}."


def _lesson_block(context: dict) -> str:
    lesson = context.get("current_lesson") or {}
    if not isinstance(lesson, dict) or not lesson.get("name"):
        return ""
    content = str(lesson.get("body") or lesson.get("content") or "").strip()
    if not content:
        return f"Bài đang mở: {lesson.get('title') or lesson.get('name')}; nội dung chưa có trong bản ghi."
    return (
        f"Nội dung bài đang mở ({lesson.get('title') or lesson.get('name')}) được nạp qua Frappe, "
        "chỉ dùng để trả lời về bài này và không tự suy diễn ngoài nội dung:\n" + content
    )


def learner_context_for_prompt(
    user: str, course: str = "", lesson: str = "", limit: int = 3
) -> dict:
    """Gom learner state cho prompt F1: weak approved concepts + concept bài mở."""
    user = str(user or "").strip()
    course = str(course or "").strip()
    lesson = str(lesson or "").strip()
    if not user or user == "Guest":
        return {"learner_states": [], "lesson_concepts": []}
    try:
        weak = weak_concepts(user, course, limit=limit)
    except Exception:
        weak = []
    try:
        lesson_concepts = concepts_for_lesson(lesson) if lesson else []
    except Exception:
        lesson_concepts = []
    return {"learner_states": weak, "lesson_concepts": lesson_concepts}


def _initial_messages(system: str, user_msg: str, context: dict) -> list[dict]:
    """Build the prompt with bounded recent context, never allowing it to replace system rules."""
    messages: list[dict] = [{"role": "system", "content": system}]
    block = _memory_block(context)
    if block:
        messages.append({"role": "system", "content": block})
    tutor = _learner_block(context)
    if tutor:
        messages.append({"role": "system", "content": tutor})
    route = _route_block(context)
    if route:
        messages.append({"role": "system", "content": route})
    lesson = _lesson_block(context)
    if lesson:
        messages.append({"role": "system", "content": lesson})
    mode_instruction = str(context.get("mode_instruction") or "").strip()
    if mode_instruction:
        messages.append({"role": "system", "content": mode_instruction})
    for item in context.get("recent_messages") or []:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        content = str(item.get("content") or "").strip()
        if content:
            messages.append({"role": item["role"], "content": content})
    messages.append({"role": "user", "content": user_msg})
    try:
        memories = context.get("long_term_memories") or []
        states = context.get("learner_states") or []
        _trace(
            "prompt", "assembled", member=str(context.get("user") or ""),
            fact_ids=[m.get("id") for m in memories if isinstance(m, dict)][:4],
            weak_concepts=[
                {"concept_id": s.get("concept_id"), "p": round(float(s.get("p_display", s.get("p", 0.0))), 4)}
                for s in states if isinstance(s, dict)
            ][:3],
            lesson_concepts=[
                c.get("concept_id", c.get("id")) for c in (context.get("lesson_concepts") or [])
                if isinstance(c, dict)
            ][:6],
            route=context.get("route") or {},
            current_lesson=bool((context.get("current_lesson") or {}).get("name")),
            user_msg=user_msg,
        )
    except Exception:
        pass
    return messages


def _run_tool(registry, allowed: list, role: str, name: str, args: dict, context: dict):
    clean_args = {
        key: value
        for key, value in dict(args or {}).items()
        if not str(key).startswith("_")
    }
    args.clear()
    args.update(clean_args)
    if context.get("user"):
        args["member"] = context["user"]
    args["_role"] = role
    tool = registry.get(name)
    if not tool or name not in allowed:
        out = {"error": f"tool '{name}' khong duoc phep cho role '{role}'"}
        return out, None
    if tool.needs_approval:
        aid = request_approval(
            name,
            args,
            requested_by=str(context.get("user") or ""),
            requested_role=role,
            required_roles=tool.approval_roles,
        )
        return {"needs_approval": True, "approval_id": aid}, {"approval_id": aid, "tool": name}
    try:
        return tool.func(args), None
    except Exception as e:  # noqa: BLE001 - tra loi ve cho LLM
        return {"error": str(e)}, None


def _tool_result_msg(call_id: str, out: dict) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(out, ensure_ascii=False)}


def run_agent(client, registry, role: str, user_msg: str, context: dict | None = None) -> dict:
    """Tra ve {answer, tool_calls, approvals, timings}. Chua approve thi dung, tra approval_id."""
    t0 = time.perf_counter()
    timings: dict = {"ttft_ms": None, "llm_ms": 0, "tools_ms": 0}
    context = context or {}
    allowed = allowed_tools(role)
    schemas = registry.schemas(allowed)

    system = (
        "Bạn là gia sư LMS biết từng người học. Chỉ dùng tool được cấp. "
        "Mọi câu trả lời về khóa học phải kèm nguồn dạng `LMS Course: <tên>, Course Lesson: <tên>`. "
        "Không bịa đặt, không truy cập dữ liệu ngoài tool. "
        "Đọc các lượt trao đổi gần đây để nối ngữ cảnh; nếu người dùng nói ‘bài này’, ‘nó’ hoặc chỉ bổ sung một phần thông tin, "
        "hãy kết hợp với yêu cầu trước đó thay vì hỏi lại. "
        "Ưu tiên nhắc đúng concept yếu đã bơm trong prompt; mỗi gợi ý phải kèm vì sao "
        "(evidence gần nhất). Nếu học viên nói ‘không đúng’, gọi record_feedback_correction. "
        "Khi người dùng nói rõ một thông tin bền vững (tên gọi, mục tiêu, trình độ, sở thích học), "
        "gọi remember_user_fact để lưu. Khi câu hỏi cần thông tin đã biết trước đây, "
        "gọi recall_user_facts trước khi trả lời. Không bao giờ lưu mật khẩu/OTP/bí mật."
    )
    messages = _initial_messages(system, user_msg, context)
    tool_calls_log: list = []
    approvals: list = []

    for _ in range(6):
        t_llm = time.perf_counter()
        res = client.chat(messages, tools=schemas or None)
        if timings["ttft_ms"] is None:
            timings["ttft_ms"] = int((time.perf_counter() - t0) * 1000)
        timings["llm_ms"] += int((time.perf_counter() - t_llm) * 1000)
        msg = res["choices"][0]["message"]
        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": msg.get("tool_calls")})

        calls = msg.get("tool_calls") or []
        if not calls:
            timings["total_ms"] = int((time.perf_counter() - t0) * 1000)
            return {"answer": msg.get("content") or "", "tool_calls": tool_calls_log, "approvals": approvals, "timings": timings}

        t_tools = time.perf_counter()
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except Exception:
                args = {}
            out, ap = _run_tool(registry, allowed, role, name, args, context)
            if ap:
                approvals.append(ap)
            audit(role, name, args, out, actor=str(context.get("user") or ""))
            tool_calls_log.append({"tool": name, "args": args, "result": out})
            messages.append(_tool_result_msg(call["id"], out))
        timings["tools_ms"] += int((time.perf_counter() - t_tools) * 1000)

        # Dừng vòng lặp; chỉ endpoint approval đã xác thực mới được thực thi tool.
        if approvals:
            timings["total_ms"] = int((time.perf_counter() - t0) * 1000)
            return {
                "answer": "Thao tác cần phê duyệt trước khi thực hiện.",
                "tool_calls": tool_calls_log,
                "approvals": approvals,
                "timings": timings,
            }

    timings["total_ms"] = int((time.perf_counter() - t0) * 1000)
    return {"answer": "Tôi đã tra cứu nhưng chưa đủ dữ liệu, bạn hỏi cụ thể hơn nhé.", "tool_calls": tool_calls_log, "approvals": approvals, "timings": timings}


def run_agent_stream(client, registry, role: str, user_msg: str, context: dict | None = None):
    """Generator SSE event. Moi vong LLM: token/thought -> round(tool). Cuoi: done.

    Event:
      {"t": "round", "n": int}                       -- bat dau vong LLM thu n
      {"t": "thought", "text": str}                  -- reasoning delta (CoT)
      {"t": "token", "text": str}                    -- answer token delta
      {"t": "tool", "tool": str, "ms": int}          -- tool vua chay xong (+ ket qua rut gon)
      {"t": "done", "answer": str, "tool_calls": [...], "approvals": [...], "timings": {...}}
    """
    t0 = time.perf_counter()
    timings: dict = {"ttft_ms": None, "llm_ms": 0, "tools_ms": 0}
    context = context or {}
    allowed = allowed_tools(role)
    schemas = registry.schemas(allowed)

    system = (
        "Bạn là gia sư LMS biết từng người học. Chỉ dùng tool được cấp. "
        "Mọi câu trả lời về khóa học phải kèm nguồn dạng `LMS Course: <tên>, Course Lesson: <tên>`. "
        "Không bịa đặt, không truy cập dữ liệu ngoài tool. "
        "Đọc các lượt trao đổi gần đây để nối ngữ cảnh; nếu người dùng nói ‘bài này’, ‘nó’ hoặc chỉ bổ sung một phần thông tin, "
        "hãy kết hợp với yêu cầu trước đó thay vì hỏi lại. "
        "Ưu tiên nhắc đúng concept yếu đã bơm trong prompt; mỗi gợi ý phải kèm vì sao "
        "(evidence gần nhất). Nếu học viên nói ‘không đúng’, gọi record_feedback_correction. "
        "Khi người dùng nói rõ một thông tin bền vững (tên gọi, mục tiêu, trình độ, sở thích học), "
        "gọi remember_user_fact để lưu. Khi câu hỏi cần thông tin đã biết trước đây, "
        "gọi recall_user_facts trước khi trả lời. Không bao giờ lưu mật khẩu/OTP/bí mật."
    )
    messages = _initial_messages(system, user_msg, context)
    tool_calls_log: list = []
    approvals: list = []
    answer = ""

    for rnd in range(6):
        yield {"t": "round", "n": rnd + 1}
        t_llm = time.perf_counter()
        streamed_msg: dict | None = None
        try:
            for ev in client.chat_stream(messages, tools=schemas or None):
                if ev["type"] == "token":
                    if timings["ttft_ms"] is None:
                        timings["ttft_ms"] = int((time.perf_counter() - t0) * 1000)
                    yield {"t": "token", "text": ev["text"]}
                elif ev["type"] == "thought":
                    yield {"t": "thought", "text": ev["text"]}
                elif ev["type"] == "message":
                    streamed_msg = ev
        except Exception as e:  # noqa: BLE001 - stream hong thi fallback non-stream
            res = client.chat(messages, tools=schemas or None)
            streamed_msg = {
                "type": "message",
                "message": res["choices"][0]["message"],
                "thought": None,
                "finish_reason": None,
            }
            yield {"t": "thought", "text": f"(stream loi, dung che do thuong: {e})"}
        timings["llm_ms"] += int((time.perf_counter() - t_llm) * 1000)
        if timings["ttft_ms"] is None:
            timings["ttft_ms"] = int((time.perf_counter() - t0) * 1000)
        msg: dict = dict((streamed_msg or {}).get("message") or {})
        if (streamed_msg or {}).get("thought"):
            yield {"t": "thought", "text": ""}
        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": msg.get("tool_calls")})

        calls = msg.get("tool_calls") or []
        if not calls:
            answer = msg.get("content") or ""
            break

        t_tools = time.perf_counter()
        for call in calls:
            name = (call.get("function") or {}).get("name", "?")
            try:
                args = json.loads((call.get("function") or {}).get("arguments") or "{}")
            except Exception:
                args = {}
            t_one = time.perf_counter()
            out, ap = _run_tool(registry, allowed, role, name, args, context)
            ms = int((time.perf_counter() - t_one) * 1000)
            if ap:
                approvals.append(ap)
            audit(role, name, args, out, actor=str(context.get("user") or ""))
            tool_calls_log.append({"tool": name, "args": args, "result": out})
            messages.append(_tool_result_msg(call.get("id", f"call_{rnd}"), out))
            try:
                preview = json.dumps(out, ensure_ascii=False, default=str)[:400]
            except Exception:
                preview = str(out)[:400]
            yield {"t": "tool", "tool": name, "ms": ms, "result": preview}
        timings["tools_ms"] += int((time.perf_counter() - t_tools) * 1000)

        if approvals:
            answer = "Thao tác cần phê duyệt trước khi thực hiện."
            break
    else:
        answer = "Tôi đã tra cứu nhưng chưa đủ dữ liệu, bạn hỏi cụ thể hơn nhé."

    timings["total_ms"] = int((time.perf_counter() - t0) * 1000)
    yield {"t": "done", "answer": answer, "tool_calls": tool_calls_log, "approvals": approvals, "timings": timings}
