"""Client-directed tools: validated navigation and closed view specs only."""
from __future__ import annotations

from gateway.runtime.tool_registry import Tool, ToolRegistry
from gateway.tools.envelope import client_directive, validate_route

__all__ = ["register", "validate_route"]


def register(reg: ToolRegistry) -> None:
    def navigate(a: dict):
        return client_directive("navigate", route=validate_route(str(a.get("route") or "")))

    reg.register(Tool(
        "navigate", "Điều hướng shell tới một route LMS nội bộ đã được whitelist.",
        {"type": "object", "properties": {"route": {"type": "string"}}, "required": ["route"]}, navigate,
    ))

    def render_view(a: dict):
        return client_directive("render_view", spec=dict(a.get("spec") or {}))

    reg.register(Tool(
        "render_view", "Hiển thị ViewSpec whitelist trong canvas của Agentic Shell.",
        {"type": "object", "properties": {"spec": {"type": "object"}}, "required": ["spec"]}, render_view,
    ))
