from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self._initialize()

    def _initialize(self) -> None:
        with self.lock, self.conn:
            self.conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS queue_items (
                    pending_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    file_id TEXT NOT NULL,
                    file_unique_id TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    file_size INTEGER,
                    received_at TEXT NOT NULL,
                    target_folder TEXT,
                    status TEXT NOT NULL DEFAULT 'queued',
                    error TEXT,
                    downloaded_path TEXT,
                    overwrite_policy TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(chat_id, message_id, file_unique_id)
                );
                CREATE TABLE IF NOT EXISTS sorter_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    folder TEXT NOT NULL,
                    status TEXT NOT NULL,
                    command TEXT NOT NULL,
                    output TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );
                """
            )
            self.conn.execute(
                "UPDATE queue_items SET status='queued', error='بازیابی پس از توقف برنامه' "
                "WHERE status='downloading'"
            )

    def close(self) -> None:
        self.conn.close()

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def add_queue_item(self, **values: Any) -> int | None:
        now = utc_now()
        with self.lock, self.conn:
            try:
                cursor = self.conn.execute(
                    """
                    INSERT INTO queue_items(
                      message_id,chat_id,file_id,file_unique_id,original_filename,
                      file_size,received_at,target_folder,status,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        values["message_id"], values["chat_id"], values["file_id"],
                        values["file_unique_id"], values["original_filename"],
                        values.get("file_size"), values.get("received_at", now),
                        values.get("target_folder") or None, "queued", now, now,
                    ),
                )
                return int(cursor.lastrowid)
            except sqlite3.IntegrityError:
                return None

    def list_items(self, statuses: tuple[str, ...] | None = None) -> list[dict]:
        if statuses:
            marks = ",".join("?" for _ in statuses)
            rows = self.conn.execute(
                f"SELECT * FROM queue_items WHERE status IN ({marks}) ORDER BY pending_id",
                statuses,
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM queue_items ORDER BY pending_id").fetchall()
        return [dict(row) for row in rows]

    def get_item(self, pending_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM queue_items WHERE pending_id=?", (pending_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_item(self, pending_id: int, **values: Any) -> None:
        if not values:
            return
        values["updated_at"] = utc_now()
        columns = ", ".join(f"{key}=?" for key in values)
        with self.lock, self.conn:
            self.conn.execute(
                f"UPDATE queue_items SET {columns} WHERE pending_id=?",
                (*values.values(), pending_id),
            )

    def remove_item(self, pending_id: int) -> bool:
        with self.lock, self.conn:
            cursor = self.conn.execute(
                "DELETE FROM queue_items WHERE pending_id=? AND status NOT IN ('downloading')",
                (pending_id,),
            )
            return cursor.rowcount > 0

    def clear_queue(self) -> int:
        with self.lock, self.conn:
            cursor = self.conn.execute(
                "DELETE FROM queue_items WHERE status IN ('queued','failed','waiting_overwrite','cancelled')"
            )
            return cursor.rowcount

    def rename_target_folder(
        self, old_name: str, new_name: str, old_path: Path, new_path: Path
    ) -> int:
        """Retarget queue records after a safe destination-folder rename."""
        old_prefix = str(old_path)
        new_prefix = str(new_path)
        with self.lock, self.conn:
            cursor = self.conn.execute(
                """
                UPDATE queue_items
                SET target_folder=?,
                    downloaded_path=CASE
                      WHEN downloaded_path IS NOT NULL
                       AND substr(downloaded_path,1,?)=?
                      THEN ? || substr(downloaded_path,?)
                      ELSE downloaded_path
                    END,
                    updated_at=?
                WHERE target_folder=?
                """,
                (
                    new_name,
                    len(old_prefix), old_prefix,
                    new_prefix, len(old_prefix) + 1,
                    utc_now(), old_name,
                ),
            )
            return cursor.rowcount

    def create_sorter_run(self, folder: str, command: str) -> int:
        with self.lock, self.conn:
            cursor = self.conn.execute(
                "INSERT INTO sorter_runs(folder,status,command,started_at) VALUES(?,?,?,?)",
                (folder, "running", command, utc_now()),
            )
            return int(cursor.lastrowid)

    def finish_sorter_run(self, run_id: int, status: str, output: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE sorter_runs SET status=?,output=?,finished_at=? WHERE id=?",
                (status, output, utc_now(), run_id),
            )

    def latest_sorter_run(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM sorter_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
