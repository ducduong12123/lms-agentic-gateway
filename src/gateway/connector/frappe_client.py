"""FrappeConnector: wrapper REST duy nhất được phép chạm Frappe. LLM không gọi trực tiếp."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request


class FrappeClient:
    def __init__(self, base_url: str, api_key: str = "", api_secret: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key and self.api_secret:
            h["Authorization"] = f"token {self.api_key}:{self.api_secret}"
        return h

    def _call(self, method: str, path: str, params: dict | None = None, body: dict | None = None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read().decode()
            return json.loads(raw) if raw else {}

    # --- generic (chỉ tool layer được gọi, không expose cho LLM) ---
    def list_documents(self, doctype: str, filters=None, fields=None, limit=20):
        p: dict = {"limit_page_length": limit}
        if filters:
            p["filters"] = json.dumps(filters)
        if fields:
            p["fields"] = json.dumps(fields)
        return self._call("GET", f"/api/resource/{urllib.parse.quote(doctype)}", params=p)

    def get_document(self, doctype: str, name: str):
        return self._call("GET", f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}")

    def create_document(self, doctype: str, doc: dict):
        return self._call("POST", f"/api/resource/{urllib.parse.quote(doctype)}", body=doc)

    def update_document(self, doctype: str, name: str, doc: dict):
        return self._call("PUT", f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}", body=doc)

    def call_method(self, dotted: str, **kwargs):
        return self._call("POST", f"/api/method/{dotted}", body=kwargs)
