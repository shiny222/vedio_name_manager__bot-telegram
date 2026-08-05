from __future__ import annotations

from .state_store import StateStore


class QueueManager:
    ACTIVE = (
        "queued",
        "failed",
        "waiting_overwrite",
        "awaiting_identification",
        "movie_import_failed",
    )

    def __init__(self, store: StateStore):
        self.store = store

    def add(self, **item) -> int | None:
        return self.store.add_queue_item(**item)

    def pending(self, chat_id: int | None = None) -> list[dict]:
        return self.store.list_items(self.ACTIVE, chat_id=chat_id)

    def downloadable(self, chat_id: int | None = None) -> list[dict]:
        return self.store.list_items(("queued", "failed"), chat_id=chat_id)

    def remove(self, pending_id: int, chat_id: int | None = None) -> bool:
        return self.store.remove_item(pending_id, chat_id=chat_id)

    def clear(self, chat_id: int | None = None) -> int:
        return self.store.clear_queue(chat_id=chat_id)

    def set_status(self, pending_id: int, status: str, error: str | None = None, **extra) -> None:
        self.store.update_item(pending_id, status=status, error=error, **extra)
