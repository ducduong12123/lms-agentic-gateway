"""Gateway server stdlib: /health, /chat, /chat/stream (SSE), /approve, /webhook/frappe, /widget/*.js.

Chạy:  python -m gateway.api.server   (từ thư mục src)
"""
from __future__ import annotations

import json
import hashlib
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gateway.config import settings  # noqa: E402
from gateway.connector.frappe_client import FrappeClient  # noqa: E402
from gateway.runtime.agent_loop import run_agent, run_agent_stream  # noqa: E402
from gateway.runtime.approval import approve  # noqa: E402
from gateway.runtime.model_client import OpenAICompatClient  # noqa: E402
from gateway.runtime.memory import short_term_memory  # noqa: E402
from gateway.tools.catalog import build_registry  # noqa: E402
from gateway.api.webhook import handle_event, verify_signature  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
WIDGET_DIR = ROOT / "widget"


def _deps():
    frappe = FrappeClient(settings.frappe_url, settings.frappe_api_key, settings.frappe_api_secret)
    llm = OpenAICompatClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
    reg = build_registry(frappe)
    return frappe, llm, reg


def _memory_context(data: dict, role: str) -> tuple[str | None, dict]:
    """Scope memory to the authenticated user, role and opaque browser conversation id."""
    conversation_id = str(data.get("conversation_id") or "").strip()[:128]
    user = str(data.get("user") or "Guest").strip()[:256]
    if not conversation_id:
        return None, {"user": data.get("user")}
    raw_key = f"{user}\0{role}\0{conversation_id}".encode("utf-8")
    key = hashlib.sha256(raw_key).hexdigest()
    return key, {"user": data.get("user"), "recent_messages": short_term_memory.get(key)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _sse(self, event: str, obj) -> None:
        chunk = ("event: " + event + "\ndata: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode()
        self.wfile.write(chunk)
        try:
            self.wfile.flush()
        except Exception:
            pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def _read(self) -> tuple[dict, bytes]:
        ln = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(ln) if ln else b""
        try:
            return (json.loads(raw.decode() or "{}"), raw)
        except Exception:
            return ({}, raw)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "*")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/health"):
            return self._json({"ok": True, "model": settings.llm_model})
        if self.path.startswith("/widget/"):
            name = urllib.parse.unquote(self.path.split("/widget/")[1].split("?")[0])
            fp = (WIDGET_DIR / name).resolve()
            if not str(fp).startswith(str(WIDGET_DIR)) or not fp.exists():
                return self._json({"error": "not found"}, 404)
            body = fp.read_bytes()
            ctype = "application/javascript" if fp.suffix == ".js" else "text/css"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        return self._json({"error": "unknown route"}, 404)

    def do_POST(self):
        data, raw = self._read()
        _, llm, reg = _deps()

        if self.path.startswith("/chat/stream"):
            role = data.get("role", "student")
            user_msg = data.get("message", "")
            memory_key, ctx = _memory_context(data, role)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                for ev in run_agent_stream(llm, reg, role, user_msg, ctx):
                    self._sse(ev.get("t", "msg"), ev)
                    if ev.get("t") == "done":
                        short_term_memory.append(memory_key, user_msg, ev.get("answer", ""))
            except (BrokenPipeError, ConnectionResetError):
                pass
            return None

        if self.path.startswith("/chat"):
            role = data.get("role", "student")
            user_msg = data.get("message", "")
            memory_key, ctx = _memory_context(data, role)
            out = run_agent(llm, reg, role, user_msg, ctx)
            short_term_memory.append(memory_key, user_msg, out.get("answer", ""))
            return self._json(out)

        if self.path.startswith("/approve/"):
            aid = self.path.split("/approve/")[1].split("?")[0]
            got = approve(aid)
            if not got:
                return self._json({"error": "approval không tồn tại"}, 404)
            # chạy lại tool đã duyệt với cờ _approved
            tool = reg.get(got["tool"])
            args = dict(got["args"] or {})
            args["_approved"] = True
            try:
                result = tool.func(args)
            except Exception as e:  # noqa: BLE001
                result = {"error": str(e)}
            return self._json({"ok": True, "tool": got["tool"], "result": result})

        if self.path.startswith("/webhook/frappe"):
            sig = self.headers.get("X-Frappe-Webhook-Signature", "")
            if not verify_signature(settings.frappe_webhook_secret, raw, sig):
                return self._json({"error": "bad signature"}, 401)
            return self._json(handle_event(dict(self.headers), raw))

        return self._json({"error": "unknown route"}, 404)


if __name__ == "__main__":
    srv = ThreadingHTTPServer((settings.gateway_host, settings.gateway_port), Handler)
    srv.daemon_threads = True
    print(f"gateway on http://{settings.gateway_host}:{settings.gateway_port}")
    print(f"widget: http://{settings.gateway_host}:{settings.gateway_port}/widget/agentic-copilot.js")
    srv.serve_forever()
