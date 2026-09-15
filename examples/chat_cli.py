"""Demo CLI, chạy được ngay cả khi chưa có LLM/Frappe (dùng fake)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.runtime.agent_loop import run_agent
from gateway.tools.catalog import build_registry


class FakeLLM:
    """Giả 1 turn tool-call để demo runtime không cần LLM key."""

    def __init__(self):
        self.n = 0

    def chat(self, messages, tools=None):
        self.n += 1
        if self.n == 1:
            return {"choices": [{"message": {"content": None, "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "search_courses", "arguments": '{"query":"python"}'}}]}}]}
        return {"choices": [{"message": {"content": "Tìm thấy: LMS Course: Python cơ bản (demo runtime OK)."}}]}


if __name__ == "__main__":
    from gateway.connector.frappe_client import FrappeClient

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # noqa: F401
    except Exception:
        pass

    reg = build_registry(FrappeClient("http://localhost"))
    out = run_agent(FakeLLM(), reg, "student", sys.argv[1] if len(sys.argv) > 1 else "demo")
    print(out["answer"])
    print("tools:", [t["tool"] for t in out["tool_calls"]])
