"""FrappeConnector: wrapper REST duy nhất được phép chạm Frappe. LLM không gọi trực tiếp."""
from __future__ import annotations
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from gateway.config import settings


_CSRF_TOKEN_RE = re.compile(r'window\["csrf_token"\]\s*=\s*"([^"]+)"')


class FrappeClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        api_secret: str = "",
        session_id: str = "",
        site_host: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.session_id = session_id
        self.site_host = site_host
        self._csrf_token_value = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.session_id or (self.api_key and self.api_secret))

    def with_session(self, session_id: str) -> "FrappeClient":
        """Return a client authenticated as the current Frappe browser session."""
        return FrappeClient(self.base_url, session_id=session_id, site_host=self.site_host)

    def _headers(self, csrf_token: str = "") -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key and self.api_secret:
            h["Authorization"] = f"token {self.api_key}:{self.api_secret}"
        if self.session_id:
            h["Cookie"] = f"sid={self.session_id}"
        if self.site_host:
            h["Host"] = self.site_host
        if csrf_token:
            h["X-Frappe-CSRF-Token"] = csrf_token
        return h

    def _csrf_token(self) -> str:
        """Read the session CSRF token from Frappe's rendered LMS shell."""
        if self._csrf_token_value or not self.session_id:
            return self._csrf_token_value
        req = urllib.request.Request(
            self.base_url + "/lms",
            headers=self._headers(),
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                html = res.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError:
            return ""
        match = _CSRF_TOKEN_RE.search(html)
        self._csrf_token_value = match.group(1) if match else ""
        return self._csrf_token_value

    def _call(self, method: str, path: str, params: dict | None = None, body: dict | None = None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        csrf_token = ""
        if self.session_id and not (self.api_key and self.api_secret) and method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            csrf_token = self._csrf_token()
        req = urllib.request.Request(url, data=data, headers=self._headers(csrf_token), method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                raw = res.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
                detail = payload.get("_server_messages") or payload.get("exception") or raw
            except (TypeError, ValueError):
                detail = raw
            raise RuntimeError(f"Frappe HTTP {exc.code}: {detail}") from exc

    # --- generic (chỉ tool layer được gọi, không expose cho LLM) ---
    def list_documents(self, doctype: str, filters=None, fields=None, limit=20, order_by: str = ""):
        p: dict = {"limit_page_length": limit}
        if filters:
            p["filters"] = json.dumps(filters)
        if fields:
            p["fields"] = json.dumps(fields)
        if order_by:
            p["order_by"] = order_by
        return self._call("GET", f"/api/resource/{urllib.parse.quote(doctype)}", params=p)

    def get_document(self, doctype: str, name: str):
        return self._call("GET", f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}")

    def create_document(self, doctype: str, doc: dict):
        return self._call("POST", f"/api/resource/{urllib.parse.quote(doctype)}", body=doc)

    def update_document(self, doctype: str, name: str, doc: dict):
        return self._call("PUT", f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}", body=doc)

    def delete_document(self, doctype: str, name: str):
        return self._call("DELETE", f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}")


    def call_method(self, dotted: str, **kwargs):
        return self._call("POST", f"/api/method/{dotted}", body=kwargs)

    def get_method(self, dotted: str, **kwargs):
        return self._call("GET", f"/api/method/{dotted}", params=kwargs)

    # --- lms_copilot (Frappe app cài cạnh lms): tool API có lọc role và ghi Tool Log ---
    def get_copilot_tools(self) -> list[dict]:
        """Catalog tool lms_copilot cho user hiện tại; site chưa cài app thì trả []."""
        try:
            message = self.get_method(f"{settings.copilot_api}.get_tools").get("message") or {}
        except RuntimeError:
            return []
        return list(message.get("tools") or [])

    def call_copilot_tool(
        self,
        tool: str,
        arguments: dict | None = None,
        conversation: str | None = None,
        model: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
    ):
        body: dict = {"tool": tool, "arguments": arguments or {}}
        for key, value in (
            ("conversation", conversation),
            ("model", model),
            ("tokens_in", tokens_in),
            ("tokens_out", tokens_out),
        ):
            if value is not None:
                body[key] = value
        return self._call("POST", f"/api/method/{settings.copilot_api}.call_tool", body=body).get("message")

    def get_copilot_weekly_insight(self, course: str, week_start: str | None = None) -> dict | None:
        """Báo cáo điểm vướng mới nhất (hoặc của tuần chứa week_start); chưa có thì None."""
        params = {"course": course}
        if week_start:
            params["week_start"] = week_start
        message = self.get_method(f"{settings.copilot_api}.get_weekly_insight", **params).get("message")
        return message if isinstance(message, dict) else None

    def rate_copilot_answer(self, conversation: str, message_index: int, helpful: bool):
        return self.call_method(
            f"{settings.copilot_api}.rate_answer",
            conversation=conversation,
            message_index=int(message_index),
            helpful=1 if helpful else 0,
        ).get("message")
