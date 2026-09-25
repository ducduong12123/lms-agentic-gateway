"""Cầu nối tới tool API của lms_copilot (Frappe app).

lms_copilot tự lọc tool theo role của user đang đăng nhập, tự kiểm tra quyền và ghi
Copilot Tool Log. Gateway chỉ lấy catalog theo session của user rồi đăng ký lại vào
ToolRegistry với tiền tố ``copilot_``. Tool ghi của lms_copilot chỉ tạo Copilot Proposal
chờ giáo viên duyệt trong Frappe, nên Gateway không bọc thêm approval của riêng mình.
"""
from __future__ import annotations

import hashlib
import threading
import time

from ..config import settings
from ..connector.frappe_client import FrappeClient
from ..runtime.tool_registry import Tool, ToolRegistry

PREFIX = "copilot_"
BUNDLE = "copilot.lms"
CATALOG_TTL_SECONDS = 60

_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def _cache_key(frappe: FrappeClient) -> str:
    identity = frappe.session_id or f"{frappe.api_key}:{frappe.api_secret}"
    return hashlib.sha256(f"{frappe.base_url}|{frappe.site_host}|{identity}".encode("utf-8")).hexdigest()


def fetch_catalog(frappe: FrappeClient) -> list[dict]:
    """Catalog có cache ngắn theo user, để mỗi lượt chat không phải gọi thêm một request."""
    # Client giả trong test hoặc mock mode không có lms_copilot: không đăng ký gì.
    if not getattr(frappe, "is_configured", False) or not hasattr(frappe, "get_copilot_tools"):
        return []
    key = _cache_key(frappe)
    now = time.time()
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
    tools = frappe.get_copilot_tools()
    with _lock:
        _cache[key] = (now + CATALOG_TTL_SECONDS, tools)
    return tools


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def is_copilot_tool(name: str) -> bool:
    return str(name or "").startswith(PREFIX)


def _make_func(frappe: FrappeClient, tool_name: str, properties: set[str]):
    def func(args: dict):
        # agent_loop tự bơm member/_role/_session_id; lms_copilot từ chối tham số lạ,
        # và danh tính đã nằm trong session nên chỉ gửi đúng các field trong schema.
        arguments = {key: value for key, value in dict(args or {}).items() if key in properties}
        try:
            result = frappe.call_copilot_tool(tool_name, arguments, model=settings.llm_model)
        except RuntimeError as exc:
            return {"error": str(exc)}
        return result if isinstance(result, dict) else {"data": result}

    return func


def register(reg: ToolRegistry, frappe: FrappeClient) -> list[str]:
    """Đăng ký các tool lms_copilot mà user này được dùng; trả về tên đã đăng ký."""
    names = []
    for item in fetch_catalog(frappe):
        tool_name = str(item.get("name") or "")
        if not tool_name:
            continue
        parameters = item.get("parameters") or {"type": "object", "properties": {}}
        properties = set((parameters.get("properties") or {}).keys())
        description = str(item.get("description") or "")
        if item.get("writes"):
            description += " Chỉ tạo bản nháp chờ giáo viên duyệt trong hàng chờ /copilot."
        name = PREFIX + tool_name
        reg.register(Tool(
            name,
            description,
            parameters,
            _make_func(frappe, tool_name, properties),
            bundles={BUNDLE},
            risk="propose" if item.get("writes") else "read",
        ))
        names.append(name)
    return names

