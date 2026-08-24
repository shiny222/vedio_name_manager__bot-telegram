from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LEGACY_CHAT_SETTINGS = {
    "current_folder",
    "latest_downloaded_file",
    "latest_downloaded_folder",
    "latest_downloaded_movie_file",
    "latest_downloaded_movie_id",
    "latest_imported_movie_id",
    "latest_movie_batch_id",
    "current_library_key",
    "latest_downloaded_library_key",
}


def chat_setting_key(chat_id: int, name: str) -> str:
    return f"chat:{int(chat_id)}:{name}"


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
                    library_key TEXT,
                    media_kind TEXT NOT NULL DEFAULT 'series',
                    movie_title TEXT,
                    movie_year INTEGER,
                    series_title TEXT,
                    series_year INTEGER,
                    series_season INTEGER,
                    series_episode INTEGER,
                    download_filename TEXT,
                    imdb_id TEXT,
                    metadata_provider TEXT,
                    metadata_provider_id TEXT,
                    movie_batch_id TEXT,
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
                    chat_id INTEGER,
                    operation_kind TEXT NOT NULL DEFAULT 'series',
                    library_key TEXT,
                    batch_id TEXT,
                    folder TEXT NOT NULL,
                    status TEXT NOT NULL,
                    command TEXT NOT NULL,
                    output TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );
                """
            )
            # Safe forward-only migrations for databases created by older bot
            # versions. Existing queue rows remain series items.
            columns = {
                str(row["name"])
                for row in self.conn.execute("PRAGMA table_info(queue_items)")
            }
            migrations = {
                "media_kind": "TEXT NOT NULL DEFAULT 'series'",
                "library_key": "TEXT",
                "movie_title": "TEXT",
                "movie_year": "INTEGER",
                "series_title": "TEXT",
                "series_year": "INTEGER",
                "series_season": "INTEGER",
                "series_episode": "INTEGER",
                "download_filename": "TEXT",
                "imdb_id": "TEXT",
                "metadata_provider": "TEXT",
                "metadata_provider_id": "TEXT",
                "movie_batch_id": "TEXT",
            }
            for name, declaration in migrations.items():
                if name not in columns:
                    self.conn.execute(
                        f"ALTER TABLE queue_items ADD COLUMN {name} {declaration}"
                    )
            sorter_columns = {
                str(row["name"])
                for row in self.conn.execute("PRAGMA table_info(sorter_runs)")
            }
            sorter_migrations = {
                "chat_id": "INTEGER",
                "operation_kind": "TEXT NOT NULL DEFAULT 'series'",
                "library_key": "TEXT",
                "batch_id": "TEXT",
            }
            for name, declaration in sorter_migrations.items():
                if name not in sorter_columns:
                    self.conn.execute(
                        f"ALTER TABLE sorter_runs ADD COLUMN {name} {declaration}"
                    )
            self._migrate_legacy_sorter_runs()
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_queue_chat_status "
                "ON queue_items(chat_id,status,pending_id)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sorter_chat_kind "
                "ON sorter_runs(chat_id,operation_kind,id)"
            )
            self.conn.execute(
                "UPDATE queue_items SET status='queued', error='Recovered after bot restart' "
                "WHERE status='downloading'"
            )

    def _migrate_legacy_sorter_runs(self) -> None:
        """Assign old single-user sorter records to their most likely owner."""
        rows = self.conn.execute(
            "SELECT id,command,output FROM sorter_runs WHERE chat_id IS NULL"
        ).fetchall()
        if not rows:
            return
        owner_row = self.conn.execute(
            "SELECT chat_id FROM queue_items ORDER BY pending_id DESC LIMIT 1"
        ).fetchone()
        legacy_owner = int(owner_row["chat_id"]) if owner_row is not None else None
        for row in rows:
            command_text = str(row["command"] or "")
            command_lower = command_text.casefold()
            output = str(row["output"] or "")
            if "movie_organizer.py" in command_lower:
                kind = "movie"
            elif '"undo-batch"' in command_lower or '"undo-last"' in command_lower:
                kind = "series_undo"
            elif any(
                marker in command_lower
                for marker in (
                    '"rename-folder"',
                    '"sort-history"',
                    '"sort-back"',
                    '"sort-forward"',
                    '"recover-folder"',
                )
            ):
                kind = "series_maintenance"
            else:
                kind = "series"
            match = re.search(
                r"(?:Resort |Metadata )?Batch ID:\s*([A-Za-z0-9._-]{1,100})",
                output,
                re.IGNORECASE,
            )
            batch_id = match.group(1) if match and kind == "series" else None
            owner = legacy_owner
            if kind == "movie":
                movie_batch = re.search(
                    r'"batch_id"\s*:\s*"([A-Za-z0-9._-]{1,100})"', output
                )
                if movie_batch:
                    movie_owner = self.conn.execute(
                        "SELECT chat_id FROM queue_items WHERE movie_batch_id=? "
                        "ORDER BY pending_id DESC LIMIT 1",
                        (movie_batch.group(1),),
                    ).fetchone()
                    if movie_owner is not None:
                        owner = int(movie_owner["chat_id"])
            self.conn.execute(
                "UPDATE sorter_runs SET chat_id=?,operation_kind=?,batch_id=? "
                "WHERE id=?",
                (owner, kind, batch_id, int(row["id"])),
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

    def get_chat_setting(self, chat_id: int, name: str, default: str = "") -> str:
        """Read chat-private state and safely claim legacy single-chat state once."""
        key = chat_setting_key(chat_id, name)
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        if row is not None:
            return str(row["value"])
        if name not in LEGACY_CHAT_SETTINGS:
            return default
        with self.lock, self.conn:
            owner_row = self.conn.execute(
                "SELECT value FROM settings WHERE key='legacy_chat_state_owner'"
            ).fetchone()
            owner = str(owner_row["value"]) if owner_row else ""
            if not owner:
                queue_owner = self.conn.execute(
                    "SELECT chat_id FROM queue_items ORDER BY pending_id DESC LIMIT 1"
                ).fetchone()
                owner = (
                    str(int(queue_owner["chat_id"]))
                    if queue_owner is not None
                    else str(int(chat_id))
                )
                self.conn.execute(
                    "INSERT INTO settings(key,value) VALUES('legacy_chat_state_owner',?)",
                    (owner,),
                )
            if owner != str(int(chat_id)):
                return default
            legacy_row = self.conn.execute(
                "SELECT value FROM settings WHERE key=?", (name,)
            ).fetchone()
            legacy = str(legacy_row["value"]) if legacy_row else ""
            if legacy:
                self.conn.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, legacy),
                )
                return legacy
        return default

    def has_chat_setting(self, chat_id: int, name: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM settings WHERE key=?",
            (chat_setting_key(chat_id, name),),
        ).fetchone()
        return row is not None

    def set_chat_setting(self, chat_id: int, name: str, value: str) -> None:
        self.set_setting(chat_setting_key(chat_id, name), value)

    def replace_chat_setting_value(
        self, name: str, old_value: str, new_value: str
    ) -> int:
        """Keep every chat's pointer valid after a shared folder is renamed."""
        with self.lock, self.conn:
            cursor = self.conn.execute(
                "UPDATE settings SET value=? WHERE key GLOB ? AND value=?",
                (new_value, f"chat:*:{name}", old_value),
            )
            return cursor.rowcount

    def replace_chat_setting_prefix(
        self, name: str, old_prefix: str, new_prefix: str
    ) -> int:
        with self.lock, self.conn:
            cursor = self.conn.execute(
                "UPDATE settings SET value=? || substr(value, ?) "
                "WHERE key GLOB ? AND substr(value, 1, ?)=?",
                (
                    new_prefix,
                    len(old_prefix) + 1,
                    f"chat:*:{name}",
                    len(old_prefix),
                    old_prefix,
                ),
            )
            return cursor.rowcount

    def replace_chat_setting_value_in_library(
        self,
        name: str,
        old_value: str,
        new_value: str,
        library_key: str,
        *,
        include_legacy: bool = False,
        library_setting_name: str = "current_library_key",
    ) -> int:
        """Update chat pointers only for chats using the renamed library."""
        changed = 0
        with self.lock, self.conn:
            rows = self.conn.execute(
                "SELECT key FROM settings WHERE key GLOB ? AND value=?",
                (f"chat:*:{name}", old_value),
            ).fetchall()
            for row in rows:
                key = str(row["key"])
                match = re.fullmatch(r"chat:(-?\d+):.+", key)
                if not match:
                    continue
                chat_id = int(match.group(1))
                selected = self.get_chat_setting(chat_id, library_setting_name)
                if selected != library_key and not (include_legacy and not selected):
                    continue
                cursor = self.conn.execute(
                    "UPDATE settings SET value=? WHERE key=? AND value=?",
                    (new_value, key, old_value),
                )
                changed += cursor.rowcount
        return changed

    def replace_chat_setting_prefix_in_library(
        self,
        name: str,
        old_prefix: str,
        new_prefix: str,
        library_key: str,
        *,
        include_legacy: bool = False,
        library_setting_name: str = "current_library_key",
    ) -> int:
        changed = 0
        with self.lock, self.conn:
            rows = self.conn.execute(
                "SELECT key,value FROM settings WHERE key GLOB ? "
                "AND substr(value,1,?)=?",
                (f"chat:*:{name}", len(old_prefix), old_prefix),
            ).fetchall()
            for row in rows:
                key = str(row["key"])
                match = re.fullmatch(r"chat:(-?\d+):.+", key)
                if not match:
                    continue
                chat_id = int(match.group(1))
                selected = self.get_chat_setting(chat_id, library_setting_name)
                if selected != library_key and not (include_legacy and not selected):
                    continue
                old_value = str(row["value"])
                new_value = new_prefix + old_value[len(old_prefix):]
                cursor = self.conn.execute(
                    "UPDATE settings SET value=? WHERE key=? AND value=?",
                    (new_value, key, old_value),
                )
                changed += cursor.rowcount
        return changed

    def add_queue_item(self, **values: Any) -> int | None:
        now = utc_now()
        with self.lock, self.conn:
            try:
                cursor = self.conn.execute(
                    """
                    INSERT INTO queue_items(
                      message_id,chat_id,file_id,file_unique_id,original_filename,
                      file_size,received_at,target_folder,library_key,media_kind,movie_title,
                      movie_year,series_title,series_year,series_season,series_episode,
                      download_filename,imdb_id,metadata_provider,metadata_provider_id,
                      movie_batch_id,status,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        values["message_id"], values["chat_id"], values["file_id"],
                        values["file_unique_id"], values["original_filename"],
                        values.get("file_size"), values.get("received_at", now),
                        values.get("target_folder") or None,
                        values.get("library_key") or None,
                        values.get("media_kind", "series"),
                        values.get("movie_title") or None,
                        values.get("movie_year"),
                        values.get("series_title") or None,
                        values.get("series_year"),
                        values.get("series_season"),
                        values.get("series_episode"),
                        values.get("download_filename") or None,
                        values.get("imdb_id") or None,
                        values.get("metadata_provider") or None,
                        values.get("metadata_provider_id") or None,
                        values.get("movie_batch_id") or None,
                        values.get("status", "queued"), now, now,
                    ),
                )
                return int(cursor.lastrowid)
            except sqlite3.IntegrityError:
                return None

    def list_items(
        self,
        statuses: tuple[str, ...] | None = None,
        chat_id: int | None = None,
    ) -> list[dict]:
        clauses: list[str] = []
        values: list[Any] = []
        if statuses:
            marks = ",".join("?" for _ in statuses)
            clauses.append(f"status IN ({marks})")
            values.extend(statuses)
        if chat_id is not None:
            clauses.append("chat_id=?")
            values.append(int(chat_id))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM queue_items{where} ORDER BY pending_id", tuple(values)
        ).fetchall()
        return [dict(row) for row in rows]

    def get_item(self, pending_id: int, chat_id: int | None = None) -> dict | None:
        query = "SELECT * FROM queue_items WHERE pending_id=?"
        values: tuple[Any, ...] = (pending_id,)
        if chat_id is not None:
            query += " AND chat_id=?"
            values += (int(chat_id),)
        row = self.conn.execute(query, values).fetchone()
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

    def remove_item(self, pending_id: int, chat_id: int | None = None) -> bool:
        owner_clause = " AND chat_id=?" if chat_id is not None else ""
        values: tuple[Any, ...] = (
            (pending_id, int(chat_id)) if chat_id is not None else (pending_id,)
        )
        with self.lock, self.conn:
            cursor = self.conn.execute(
                "DELETE FROM queue_items WHERE pending_id=? AND status IN "
                "('queued','failed','waiting_overwrite','cancelled',"
                f"'awaiting_identification'){owner_clause}",
                values,
            )
            return cursor.rowcount > 0

    def clear_queue(self, chat_id: int | None = None) -> int:
        owner_clause = " AND chat_id=?" if chat_id is not None else ""
        values: tuple[Any, ...] = (int(chat_id),) if chat_id is not None else ()
        with self.lock, self.conn:
            cursor = self.conn.execute(
                "DELETE FROM queue_items WHERE status IN "
                "('queued','failed','waiting_overwrite','cancelled',"
                f"'awaiting_identification'){owner_clause}",
                values,
            )
            return cursor.rowcount

    def rename_target_folder(
        self,
        old_name: str,
        new_name: str,
        old_path: Path,
        new_path: Path,
        library_key: str | None = None,
        include_legacy: bool = False,
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
                WHERE target_folder=? AND media_kind='series'
                  AND (? IS NULL OR library_key=? OR (?=1 AND library_key IS NULL))
                """,
                (
                    new_name,
                    len(old_prefix), old_prefix,
                    new_prefix, len(old_prefix) + 1,
                    utc_now(), old_name, library_key, library_key, int(include_legacy),
                ),
            )
            return cursor.rowcount

    def latest_movie_item(
        self,
        statuses: tuple[str, ...] | None = None,
        chat_id: int | None = None,
    ) -> dict | None:
        values: list[Any] = []
        where = "WHERE media_kind='movie'"
        if chat_id is not None:
            where += " AND chat_id=?"
            values.append(chat_id)
        if statuses:
            marks = ",".join("?" for _ in statuses)
            where += f" AND status IN ({marks})"
            values.extend(statuses)
        row = self.conn.execute(
            f"SELECT * FROM queue_items {where} ORDER BY pending_id DESC LIMIT 1",
            tuple(values),
        ).fetchone()
        return dict(row) if row else None

    def mark_movie_batch_status(
        self, batch_id: str, status: str, chat_id: int | None = None
    ) -> int:
        if status not in {"movie_undone", "movie_undo_partial"}:
            raise ValueError("Invalid movie undo status.")
        with self.lock, self.conn:
            owner_clause = " AND chat_id=?" if chat_id is not None else ""
            values: tuple[Any, ...] = (status, utc_now(), batch_id)
            if chat_id is not None:
                values += (int(chat_id),)
            cursor = self.conn.execute(
                "UPDATE queue_items SET status=?,updated_at=? "
                f"WHERE media_kind='movie' AND movie_batch_id=?{owner_clause}",
                values,
            )
            return cursor.rowcount

    def movie_batch_belongs_to_chat(self, batch_id: str, chat_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM queue_items WHERE media_kind='movie' "
            "AND movie_batch_id=? AND chat_id=? LIMIT 1",
            (batch_id, int(chat_id)),
        ).fetchone()
        return row is not None

    def latest_movie_batch(self, chat_id: int) -> str:
        row = self.conn.execute(
            "SELECT movie_batch_id FROM queue_items WHERE media_kind='movie' "
            "AND chat_id=? AND status IN ('imported','movie_undo_partial') "
            "AND movie_batch_id IS NOT NULL AND movie_batch_id<>'' "
            "ORDER BY pending_id DESC LIMIT 1",
            (int(chat_id),),
        ).fetchone()
        return str(row["movie_batch_id"]) if row else ""

    def mark_movie_batch_undone(self, batch_id: str) -> int:
        return self.mark_movie_batch_status(batch_id, "movie_undone")

    def create_sorter_run(
        self,
        folder: str,
        command: str,
        chat_id: int | None = None,
        operation_kind: str = "series",
        library_key: str | None = None,
    ) -> int:
        with self.lock, self.conn:
            cursor = self.conn.execute(
                "INSERT INTO sorter_runs(chat_id,operation_kind,library_key,folder,status,command,started_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    chat_id,
                    operation_kind,
                    library_key,
                    folder,
                    "running",
                    command,
                    utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def finish_sorter_run(
        self,
        run_id: int,
        status: str,
        output: str,
        batch_id: str | None = None,
    ) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE sorter_runs SET status=?,output=?,finished_at=?,batch_id=? "
                "WHERE id=?",
                (status, output, utc_now(), batch_id, run_id),
            )

    def latest_sorter_run(
        self,
        chat_id: int | None = None,
        operation_kind: str | None = None,
    ) -> dict | None:
        clauses: list[str] = []
        values: list[Any] = []
        if chat_id is not None:
            clauses.append("chat_id=?")
            values.append(int(chat_id))
        if operation_kind is not None:
            clauses.append("operation_kind=?")
            values.append(operation_kind)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        row = self.conn.execute(
            f"SELECT * FROM sorter_runs{where} ORDER BY id DESC LIMIT 1",
            tuple(values),
        ).fetchone()
        return dict(row) if row else None

    def latest_series_sorter_run(self, chat_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM sorter_runs WHERE chat_id=? "
            "AND operation_kind GLOB 'series*' ORDER BY id DESC LIMIT 1",
            (int(chat_id),),
        ).fetchone()
        return dict(row) if row else None

    def latest_sorter_batch(
        self, chat_id: int, library_key: str | None = None
    ) -> str:
        library_clause = " AND library_key=?" if library_key else ""
        values: tuple[Any, ...] = (int(chat_id),)
        if library_key:
            values += (library_key,)
        row = self.conn.execute(
            "SELECT batch_id FROM sorter_runs WHERE chat_id=? "
            "AND operation_kind='series' "
            + library_clause
            + " "
            "AND status IN ('completed','failed','undo_partial') "
            "AND batch_id IS NOT NULL AND batch_id<>'' ORDER BY id DESC LIMIT 1",
            values,
        ).fetchone()
        return str(row["batch_id"]) if row else ""

    def mark_sorter_batch_status(
        self, batch_id: str, chat_id: int, status: str
    ) -> int:
        if status not in {"undone", "undo_partial"}:
            raise ValueError("Invalid sorter batch status.")
        with self.lock, self.conn:
            cursor = self.conn.execute(
                "UPDATE sorter_runs SET status=?,finished_at=? WHERE batch_id=? "
                "AND chat_id=? AND operation_kind='series'",
                (status, utc_now(), batch_id, int(chat_id)),
            )
            return cursor.rowcount

    def sorter_batch_belongs_to_chat(self, batch_id: str, chat_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sorter_runs WHERE batch_id=? AND chat_id=? "
            "AND operation_kind='series' LIMIT 1",
            (batch_id, int(chat_id)),
        ).fetchone()
        return row is not None

    def sorter_batch_library(self, batch_id: str, chat_id: int) -> str:
        row = self.conn.execute(
            "SELECT library_key FROM sorter_runs WHERE batch_id=? AND chat_id=? "
            "AND operation_kind='series' ORDER BY id DESC LIMIT 1",
            (batch_id, int(chat_id)),
        ).fetchone()
        return str(row["library_key"] or "") if row else ""

    def movie_batch_library(self, batch_id: str, chat_id: int) -> str:
        row = self.conn.execute(
            "SELECT library_key FROM queue_items WHERE movie_batch_id=? AND chat_id=? "
            "AND media_kind='movie' ORDER BY pending_id DESC LIMIT 1",
            (batch_id, int(chat_id)),
        ).fetchone()
        return str(row["library_key"] or "") if row else ""
