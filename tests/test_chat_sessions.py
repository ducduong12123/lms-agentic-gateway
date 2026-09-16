from __future__ import annotations

import pytest

from gateway.runtime import features


def test_chat_session_crud_and_reopen(tmp_path):
    db = tmp_path / "sessions.db"
    created = features.ensure_chat_session("hv@test.com", "", "Hello Python", path=db)
    assert created["id"].startswith("cht_")
    assert created["title"] == "Hello Python"

    # Mở lại bằng conversation_id cũ phải ra đúng session.
    reopened = features.ensure_chat_session("hv@test.com", created["id"], path=db)
    assert reopened["id"] == created["id"]

    features.append_chat_turn("hv@test.com", created["id"], "Hello", "Hi bạn", path=db)
    features.append_chat_turn("hv@test.com", created["id"], "Tiếp", "OK", path=db)

    detail = features.get_chat_session("hv@test.com", created["id"], path=db)
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
    assert detail["messages"][0]["content"] == "Hello"

    listed = features.list_chat_sessions("hv@test.com", path=db)
    assert len(listed) == 1
    assert listed[0]["turns"] == 4

    assert features.rename_chat_session("hv@test.com", created["id"], "Đổi tên", path=db)
    assert features.get_chat_session("hv@test.com", created["id"], path=db)["session"]["title"] == "Đổi tên"

    assert features.delete_chat_session("hv@test.com", created["id"], path=db)
    assert features.get_chat_session("hv@test.com", created["id"], path=db) is None
    assert features.list_chat_sessions("hv@test.com", path=db) == []

def test_chat_session_limit_returns_newest_messages_in_chronological_order(tmp_path):
    db = tmp_path / "sessions.db"
    created = features.ensure_chat_session("hv@test.com", "", "History", path=db)
    for index in range(12):
        features.append_chat_turn(
            "hv@test.com", created["id"], f"user-{index}", f"assistant-{index}", path=db,
        )

    detail = features.get_chat_session("hv@test.com", created["id"], limit=4, path=db)

    assert [message["content"] for message in detail["messages"]] == [
        "user-10", "assistant-10", "user-11", "assistant-11",
    ]


def test_lesson_draft_and_workflow_state_survive_memory_restart(tmp_path):
    db = tmp_path / "sessions.db"
    created = features.ensure_chat_session("teacher@test.com", "", "Bài Python", path=db)
    body = "# Bài 2: Điều kiện trong Python\n\n" + ("Nội dung bài học. " * 30)

    features.append_chat_turn(
        "teacher@test.com",
        created["id"],
        "soạn cho tôi bài 2",
        body + "\nLMS Course: py-101\nChapter: CH-1",
        path=db,
    )

    workflow = features.workflow_context("teacher@test.com", created["id"], path=db)
    assert workflow["active_course"] == "py-101"
    assert workflow["active_chapter"] == "CH-1"
    assert workflow["active_draft"]["title"] == "Bài 2: Điều kiện trong Python"
    assert workflow["active_draft"]["body"] == body.rstrip()


def test_chat_session_isolated_per_user(tmp_path):
    db = tmp_path / "sessions.db"
    mine = features.ensure_chat_session("hv1@test.com", "", "Của tôi", path=db)

    # User khác không thấy, không mở, không xóa được.
    assert features.list_chat_sessions("hv2@test.com", path=db) == []
    assert features.get_chat_session("hv2@test.com", mine["id"], path=db) is None
    assert features.delete_chat_session("hv2@test.com", mine["id"], path=db) is False
    with pytest.raises(ValueError):
        features.append_chat_turn("hv2@test.com", mine["id"], "Hi", "Hi", path=db)

    assert features.get_chat_session("hv1@test.com", mine["id"], path=db) is not None


def test_chat_session_guest_blocked_and_erase_cleans(tmp_path):
    db = tmp_path / "sessions.db"
    with pytest.raises(ValueError):
        features.ensure_chat_session("Guest", "", "Hi", path=db)
    assert features.list_chat_sessions("Guest", path=db) == []

    created = features.ensure_chat_session("hv@test.com", "", "Hi", path=db)
    features.append_chat_turn("hv@test.com", created["id"], "Hi", "Hello", path=db)
    result = features.erase_member("hv@test.com", path=db)
    assert result["deleted_rows"] >= 3
    assert features.list_chat_sessions("hv@test.com", path=db) == []
