"""Short-term in-memory conversation buffer for the agent gateway."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass


@dataclass
class _Conversation:
    touched_at: float
    messages: list[dict[str, str]]


class ShortTermMemory:
    """Bounded, process-local memory used only to resolve recent context."""

    def __init__(self, ttl_seconds: int = 1800, max_turns: int = 8, max_chars: int = 12000):
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.max_turns = max(1, int(max_turns))
        self.max_chars = max(1000, int(max_chars))
        self._items: OrderedDict[str, _Conversation] = OrderedDict()
        self._lock = threading.RLock()

    def _purge_locked(self, now: float) -> None:
        expired = [
            key for key, item in self._items.items()
            if now - item.touched_at > self.ttl_seconds
        ]
        for key in expired:
            self._items.pop(key, None)

    def get(self, key: str | None) -> list[dict[str, str]]:
        """Return a defensive copy of recent user/assistant messages."""
        if not key:
            return []
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            item = self._items.get(key)
            if not item:
                return []
            item.touched_at = now
            self._items.move_to_end(key)
            return [dict(message) for message in item.messages]

    def append(self, key: str | None, user_message: str, assistant_message: str) -> None:
        """Append one completed turn and trim old content to the configured bounds."""
        if not key:
            return
        user_message = str(user_message or "").strip()
        assistant_message = str(assistant_message or "").strip()
        if not user_message and not assistant_message:
            return

        now = time.time()
        with self._lock:
            self._purge_locked(now)
            item = self._items.setdefault(key, _Conversation(now, []))
            item.touched_at = now
            item.messages.extend([
                {"role": "user", "content": user_message[:4000]},
                {"role": "assistant", "content": assistant_message[:6000]},
            ])
            # A turn is a user+assistant pair. Keep only complete recent pairs.
            item.messages = item.messages[-self.max_turns * 2:]
            while self._message_chars(item.messages) > self.max_chars and len(item.messages) > 2:
                item.messages = item.messages[2:]
            self._items.move_to_end(key)

    @staticmethod
    def _message_chars(messages: list[dict[str, str]]) -> int:
        return sum(len(message.get("content", "")) for message in messages)

    def clear(self, key: str | None) -> None:
        if not key:
            return
        with self._lock:
            self._items.pop(key, None)

    def size(self) -> int:
        with self._lock:
            self._purge_locked(time.time())
            return len(self._items)


short_term_memory = ShortTermMemory()
