"""Config: doc tu env (co .env stdlib-only), co default chay local duoc ngay."""
from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    """Nap file .env canh repo root (cha cua src/) neu co; env that luon thang."""
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv()


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


class Settings:
    gateway_host: str = _get("GATEWAY_HOST", "127.0.0.1")
    gateway_port: int = int(_get("GATEWAY_PORT", "8001") or 8001)

    llm_base_url: str = _get("LLM_BASE_URL", "http://127.0.0.1:20128/v1").rstrip("/")
    llm_api_key: str = _get("LLM_API_KEY", "ollama")
    llm_model: str = _get("LLM_MODEL", "cx/gpt-5.6-luna")

    frappe_url: str = _get("FRAPPE_URL", "http://learning.test:8000").rstrip("/")
    frappe_api_key: str = _get("FRAPPE_API_KEY", "")
    frappe_api_secret: str = _get("FRAPPE_API_SECRET", "")
    frappe_webhook_secret: str = _get("FRAPPE_WEBHOOK_SECRET", "")


settings = Settings()
