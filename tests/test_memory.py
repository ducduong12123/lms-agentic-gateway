from __future__ import annotations

import time

from gateway.runtime.memory import ShortTermMemory
from gateway.runtime.agent_loop import _initial_messages


def test_memory_keeps_recent_complete_turns_and_trims_oldest():
    memory = ShortTermMemory(ttl_seconds=60, max_turns=2, max_chars=1000)
    memory.append("conversation", "first", "answer 1")
    memory.append("conversation", "second", "answer 2")
    memory.append("conversation", "third", "answer 3")

    assert [item["content"] for item in memory.get("conversation")] == [
        "second", "answer 2", "third", "answer 3"
    ]


def test_memory_expires_without_persisting_to_disk():
    memory = ShortTermMemory(ttl_seconds=60, max_turns=2, max_chars=1000)
    memory.append("conversation", "question", "answer")
    memory._items["conversation"].touched_at = time.time() - 61

    assert memory.get("conversation") == []
    assert memory.size() == 0


def test_agent_prompt_includes_recent_context_before_follow_up():
    messages = _initial_messages(
        "system rules",
        "khóa học là nhập môn công nghệ giáo dục",
        {"recent_messages": [
            {"role": "user", "content": "Tạo 3 câu quiz nháp cho bài này"},
            {"role": "assistant", "content": "Vui lòng cho tên khóa học"},
        ]},
    )

    assert [message["content"] for message in messages] == [
        "system rules",
        "Tạo 3 câu quiz nháp cho bài này",
        "Vui lòng cho tên khóa học",
        "khóa học là nhập môn công nghệ giáo dục",
    ]
