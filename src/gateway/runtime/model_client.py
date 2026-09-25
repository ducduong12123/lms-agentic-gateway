"""OpenAI-compatible client. Chi dung stdlib (urllib). Ho tro chat thuong + SSE stream."""
from __future__ import annotations
import json
import urllib.error
import urllib.request


class ModelClientError(RuntimeError):
    def __init__(self, status_code: int | None, detail: str):
        self.status_code = status_code
        self.detail = str(detail or "").strip() or "unknown model error"
        prefix = f"LLM HTTP {status_code}" if status_code is not None else "LLM connection error"
        super().__init__(f"{prefix}: {self.detail}")


def _error_detail(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return raw.strip()
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error.get("detail") or error)
    if isinstance(error, str):
        return error
    if isinstance(payload, dict):
        return str(payload.get("detail") or payload.get("message") or raw).strip()
    return raw.strip()


class OpenAICompatClient:
    """Goi POST {base_url}/chat/completions, ho tro tools (function calling) + stream SSE."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _req(self, payload: dict) -> urllib.request.Request:
        return urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

    def _open(self, payload: dict):
        try:
            return urllib.request.urlopen(self._req(payload), timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            raise ModelClientError(exc.code, _error_detail(raw) or exc.reason) from exc
        except urllib.error.URLError as exc:
            raise ModelClientError(None, str(exc.reason or exc)) from exc


    def chat(self, messages: list, tools: list | None = None, effort: str = "auto") -> dict:
        payload: dict = {"model": self.model, "messages": messages}
        if effort and effort != "auto":
            payload["reasoning_effort"] = effort
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        with self._open(payload) as res:
            return json.loads(res.read().decode())

    def chat_stream(self, messages: list, tools: list | None = None, effort: str = "auto"):
        """Yield event dict. Cuoi cung luon yield {"type": "message", ...}.

        Event giua chung:
          {"type": "token", "text": str}    -- content delta (tra ve)
          {"type": "thought", "text": str}  -- reasoning delta (neu model tra ve)
          {"type": "round", ...}            -- khong co o day; agent_loop tu phat.
        """
        payload: dict = {"model": self.model, "messages": messages, "stream": True}
        if effort and effort != "auto":
            payload["reasoning_effort"] = effort
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        content_parts: list[str] = []
        thought_parts: list[str] = []
        tool_acc: dict[int, dict] = {}  # index -> {id, name, args}
        finish_reason: str | None = None
        usage: dict | None = None
        with self._open(payload) as res:
            for raw in res:
                try:
                    line = raw.decode("utf-8", "replace").strip()
                except Exception:
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except Exception:
                    continue
                if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
                    usage = obj["usage"]
                try:
                    choice = (obj.get("choices") or [{}])[0]
                except Exception:
                    continue
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                delta = choice.get("delta") or {}
                for key in ("reasoning_content", "reasoning"):
                    r = delta.get(key)
                    t = r.get("text") if isinstance(r, dict) else r
                    if isinstance(t, str) and t:
                        thought_parts.append(t)
                        yield {"type": "thought", "text": t}
                text = delta.get("content")
                if text:
                    content_parts.append(text)
                    yield {"type": "token", "text": text}
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = tool_acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]
        calls = []
        for idx in sorted(tool_acc):
            s = tool_acc[idx]
            calls.append(
                {
                    "id": s["id"] or f"call_stream_{idx}",
                    "type": "function",
                    "function": {"name": s["name"], "arguments": s["args"] or "{}"},
                }
            )
        yield {
            "type": "message",
            "message": {"content": "".join(content_parts) or None, "tool_calls": calls or None},
            "thought": "".join(thought_parts) or None,
            "finish_reason": finish_reason,
            "usage": usage,
        }
