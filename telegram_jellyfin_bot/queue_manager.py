from __future__ import annotations

from .state_store import StateStore


class QueueManager:
    ACTIVE = ("queued", "failed", "waiting_overwrite")

    def __init__(self, store: StateStore):
        self.store = store

    def add(self, **item) -> int | None:
        return self.store.add_queue_item(**item)

    def pending(self) -> list[dict]:
        return self.store.list_items(self.ACTIVE)

    def downloadable(self) -> list[dict]:
        return self.store.list_items(("queued", "failed"))

    def remove(self, pending_id: int) -> bool:
        return self.store.remove_item(pending_id)

    def clear(self) -> int:
        return self.store.clear_queue()

    def set_status(self, pending_id: int, status: str, error: str | None = None, **extra) -> None:
        self.store.update_item(pending_id, status=status, error=error, **extra)
