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
    frappe_site_host: str = _get("FRAPPE_SITE_HOST", "lms.localhost")
    frappe_api_key: str = _get("FRAPPE_API_KEY", "")
    frappe_api_secret: str = _get("FRAPPE_API_SECRET", "")
    frappe_webhook_secret: str = _get("FRAPPE_WEBHOOK_SECRET", "")

    public_origins: tuple[str, ...] = tuple(
        origin.strip().rstrip("/")
        for origin in _get(
            "PUBLIC_ORIGINS",
            "http://localhost:8080,http://lms.localhost:8080,http://localhost:8000,http://lms.localhost:8000,http://127.0.0.1:8080,http://127.0.0.1:8000",
        ).split(",")
        if origin.strip()
    )
    identity_cache_ttl: int = int(_get("IDENTITY_CACHE_TTL", "60") or 60)
    poll_interval_seconds: int = int(_get("POLL_INTERVAL_SECONDS", "900") or 900)
    scheduler_timezone: str = _get("SCHEDULER_TIMEZONE", "Asia/Ho_Chi_Minh")
    daily_plan_hour: int = int(_get("DAILY_PLAN_HOUR", "7") or 7)
    transcript_retention_days: int = int(_get("TRANSCRIPT_RETENTION_DAYS", "90") or 90)
    # Job gọi từ lịch/cron ngoài dùng Bearer này và chạy bằng tài khoản AI Engine (FRAPPE_API_KEY).
    copilot_job_key: str = _get("COPILOT_JOB_KEY", "")
    weekly_report_hour: int = int(_get("WEEKLY_REPORT_HOUR", "7") or 7)
    # lms_copilot chưa có tool liệt kê khóa học, nên lịch báo cáo tuần đọc danh sách từ env.
    copilot_weekly_courses: tuple[str, ...] = tuple(
        course.strip() for course in _get("COPILOT_WEEKLY_COURSES", "").split(",") if course.strip()
    )


settings = Settings()
