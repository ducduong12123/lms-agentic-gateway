"""ToolRegistry: nơi đăng ký business tool, LLM chỉ thấy chỗ này."""
from __future__ import annotations

from collections.abc import Callable


class Tool:
    def __init__(
        self,
        name,
        description,
        parameters,
        func: Callable,
        needs_approval=False,
        approval_roles: set[str] | None = None,
        bundles: set[str] | None = None,
        risk: str = "read",
    ):
        self.name = name
        self.description = description
        self.parameters = parameters  # JSON schema
        self.func = func
        self.needs_approval = needs_approval
        self.approval_roles = approval_roles or {"admin"}
        self.bundles = set(bundles or ())
        self.risk = risk

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(
        self,
        allowed: list[str] | None = None,
        bundles: set[str] | list[str] | None = None,
    ) -> list[dict]:
        selected = set(bundles or ())
        out = []
        for name, tool in self._tools.items():
            if allowed is not None and name not in allowed:
                continue
            if selected and tool.bundles and not tool.bundles.intersection(selected):
                continue
            out.append(tool.openai_schema())
        return out

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def assign_bundle(self, name: str, bundle: str) -> None:
        tool = self.get(name)
        if tool is None:
            raise KeyError(f"unknown tool in bundle '{bundle}': {name}")
        tool.bundles.add(bundle)

    def tools_for_bundles(self, bundles: set[str] | list[str]) -> list[str]:
        selected = set(bundles)
        return [
            name for name, tool in self._tools.items()
            if tool.bundles.intersection(selected)
        ]
