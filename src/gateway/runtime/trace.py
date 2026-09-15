"""Memory trace JSONL: log ghi-nhớ để test/kiểm tra lại, không hiện UI.

Mỗi dòng là 1 JSON object:
{"ts":..., "layer":"fact|its|prompt", "event":"...", "member":..., ...}

- Append-only, không xoay file tự động để tránh mất trace khi debug.
- Không log nội dung bí mật: chỉ cắt ngắn text/memory để lần vết.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

TRACE = Path(__file__).resolve().parents[3] / "data" / "memory_trace.jsonl"


def _short(value: Any, limit: int = 300) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def trace(
    layer: str,
    event: str,
    member: str = "",
    trace_path: Path | None = None,
    **fields: Any,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "ts": time.time(),
        "layer": str(layer or ""),
        "event": str(event or ""),
        "member": str(member or ""),
    }
    for key, value in fields.items():
        if key in {"text", "title", "query", "summary"}:
            record[key] = _short(value)
        else:
            record[key] = value
    path = Path(trace_path) if trace_path else TRACE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def read_trace(
    member: str = "",
    layer: str = "",
    event: str = "",
    limit: int = 200,
    trace_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Đọc ngược trace mới nhất trước, lọc theo member/layer/event."""
    path = Path(trace_path) if trace_path else TRACE
    if not path.exists():
        return []
    member = str(member or "")
    layer = str(layer or "")
    event = str(event or "")
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if member and str(record.get("member") or "") != member:
                continue
            if layer and str(record.get("layer") or "") != layer:
                continue
            if event and str(record.get("event") or "") != event:
                continue
            out.append(record)
    out.sort(key=lambda item: float(item.get("ts") or 0.0), reverse=True)
    return out[: max(1, min(2000, int(limit or 200)))]


def erase_member_trace(member: str, trace_path: Path | None = None) -> int:
    """Remove trace rows for one member without touching other audit evidence."""
    path = Path(trace_path) if trace_path else TRACE
    if not path.exists():
        return 0
    kept, deleted = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            kept.append(line)
            continue
        if str(record.get("member") or "") == str(member or ""):
            deleted += 1
        else:
            kept.append(line)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
    temp.replace(path)
    return deleted
