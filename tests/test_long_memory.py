from __future__ import annotations

from gateway.runtime.agent_loop import _initial_messages
from gateway.runtime.long_memory import (
    add_memory,
    forget_memory,
    format_memories_block,
    search_memories,
)


def test_long_memory_recall_prefers_lexical_and_vector_match(tmp_path):
    db = tmp_path / "memory.db"
    first = add_memory("hv1@test.com", "Tôi thích học Python vào buổi tối.", path=db)
    add_memory("hv1@test.com", "Tôi ghét môn lịch sử khô khan.", path=db)

    results = search_memories("hv1@test.com", "buổi tối học Python", path=db)

    assert results[0]["id"] == first["id"]
    assert "Python" in results[0]["text"]


def test_long_memory_scoped_per_user_and_forgettable(tmp_path):
    db = tmp_path / "memory.db"
    add_memory("hv1@test.com", "Tôi thích học Python.", path=db)
    other = add_memory("hv2@test.com", "Tôi thích học Python.", path=db)

    mine = search_memories("hv1@test.com", "Python", path=db)
    assert [item["id"] for item in mine] != [other["id"]]

    # User khác không xóa được ký ức của mình.
    assert forget_memory("hv2@test.com", mine[0]["id"], path=db) is False
    assert search_memories("hv1@test.com", "Python", path=db)

    assert forget_memory("hv1@test.com", mine[0]["id"], path=db) is True
    assert search_memories("hv1@test.com", "Python", path=db) == []


def test_long_memory_guest_is_isolated(tmp_path):
    db = tmp_path / "memory.db"
    try:
        add_memory("Guest", "Tôi thích học Python.", path=db)
    except ValueError:
        pass
    assert search_memories("Guest", "Python", path=db) == []


def test_agent_prompt_includes_long_term_memories():
    messages = _initial_messages(
        "system rules",
        "gợi ý lộ trình cho tôi",
        {
            "recent_messages": [],
            "long_term_memories": [
                {"title": "", "text": "Người học thích Python buổi tối."}
            ],
        },
    )

    assert messages[0]["content"] == "system rules"
    assert "thích Python" in messages[1]["content"]
    assert messages[-1]["content"] == "gợi ý lộ trình cho tôi"


def test_format_memories_block_empty():
    assert format_memories_block([]) == ""
