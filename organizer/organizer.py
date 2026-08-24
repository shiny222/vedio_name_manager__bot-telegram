#!/usr/bin/env python3
"""Safely organize downloaded TV episodes into a Jellyfin library."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

try:
    from guessit import guessit
except ImportError:  # The regex detector remains fully usable without guessit.
    guessit = None


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".webm", ".m4v"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".vtt"}
NFO_EXTENSIONS = {".nfo"}
ARTWORK_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
HISTORY_NAME = ".rename_history.json"
FOLDER_HISTORY_NAME = ".folder_rename_history.json"
REVISION_HISTORY_NAME = ".sort_revisions.json"
JOURNAL_NAME = ".operation_journal.jsonl"
LOG = logging.getLogger("jellyfin-organizer")


@dataclass
class HistoryRecord:
    timestamp: str
    original_full_path: str
    new_full_path: str
    original_filename: str
    new_filename: str
    file_size: int
    file_type: str
    status: str
    batch_id: str
    operation: str = "organize"
    operation_id: str = ""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def series_file_title(folder_name: str) -> str:
    """Keep year/provider metadata on the folder, but not episode filenames."""
    title = re.sub(
        r"\s*\[(?:imdbid|tmdbid|tvdbid|anilistid)-[^\]]+\]\s*",
        " ",
        folder_name,
        flags=re.IGNORECASE,
    )
    # Fuzzy-search names use Jellyfin's recommended:
    # "Official Title (2026) [imdbid-tt123]".  Episode files should contain
    # only "Official Title - S01E01.ext".
    title = re.sub(r"\s*[\(\[](?:19|20)\d{2}[\)\]]\s*$", " ", title)
    return re.sub(r"\s+", " ", title).strip() or folder_name


def load_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("history root is not a JSON array")
        return data
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        LOG.error("Cannot read history %s: %s", path, exc)
        return []


def save_history(path: Path, records: list[dict]) -> bool:
    """Atomically save history so an interrupted write does not corrupt it."""
    temp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(path)
        return True
    except OSError as exc:
        LOG.error("Cannot write history %s: %s", path, exc)
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def append_history(path: Path, record: HistoryRecord) -> bool:
    # Never replace unreadable audit data with a new, apparently valid history.
    if path.exists():
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                raise ValueError("history root is not a JSON array")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            LOG.error("Refusing to replace invalid history %s: %s", path, exc)
            return False
    else:
        records = []
    records.append(asdict(record))
    return save_history(path, records)


def append_journal(folder: Path, phase: str, details: dict) -> bool:
    """Durably append one operation phase without rewriting earlier audit data."""
    path = folder / JOURNAL_NAME
    event = {**details, "journal_timestamp": now_iso(), "phase": phase}
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return True
    except OSError as exc:
        LOG.error("Cannot write operation journal %s: %s", path, exc)
        return False


def _move_is_verified(source: Path, destination: Path, expected_size: int) -> bool:
    """Confirm that one move finished at exactly the expected destination."""
    if source.exists() or not destination.is_file():
        return False
    try:
        return destination.stat().st_size == expected_size
    except OSError:
        return False


def _rollback_move(destination: Path, source: Path) -> bool:
    """Best-effort reversal used when verification or history persistence fails."""
    try:
        if source.exists() or not destination.exists():
            return False
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(destination), str(source))
        return source.exists() and not destination.exists()
    except OSError as exc:
        LOG.critical("Could not roll back %s -> %s: %s", destination, source, exc)
        return False


def _path_key(path: Path) -> Path:
    return path.resolve(strict=False)


def _volume_key(path: Path) -> str:
    return _path_key(path).anchor.casefold()


def _copy_space_required(source: Path, destination: Path, file_size: int) -> int:
    """A same-volume move is a rename; a cross-volume move needs a full copy."""
    return 0 if _volume_key(source) == _volume_key(destination) else file_size


def _destination_free_space(destination: Path) -> int | None:
    probe = _path_key(destination.parent)
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            return None
        probe = parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def _format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"


@dataclass
class DryRunState:
    """Virtual destination and disk-space state shared by one preview batch."""

    reserved_destinations: set[Path] = field(default_factory=set)
    vacated_sources: set[Path] = field(default_factory=set)
    planned_copy_bytes: dict[str, int] = field(default_factory=dict)

    def exists(self, path: Path) -> bool:
        key = _path_key(path)
        return key in self.reserved_destinations or (
            path.exists() and key not in self.vacated_sources
        )

    def reserve(self, source: Path, destination: Path) -> bool:
        destination_key = _path_key(destination)
        if self.exists(destination):
            LOG.error("Dry-run destination is already reserved: %s", destination)
            return False
        try:
            file_size = source.stat().st_size
        except OSError as exc:
            LOG.error("Cannot inspect source size during dry-run %s: %s", source, exc)
            return False
        required = _copy_space_required(source, destination, file_size)
        if required:
            volume = _volume_key(destination)
            free = _destination_free_space(destination)
            already_planned = self.planned_copy_bytes.get(volume, 0)
            remaining = None if free is None else max(0, free - already_planned)
            if remaining is None:
                LOG.error(
                    "Cannot determine free space for dry-run destination: %s",
                    destination,
                )
                return False
            if required > remaining:
                LOG.error(
                    "NOT ENOUGH SPACE for %s: needs %s, only %s remains after "
                    "other planned moves.",
                    destination,
                    _format_bytes(required),
                    _format_bytes(remaining),
                )
                return False
            self.planned_copy_bytes[volume] = already_planned + required
        self.reserved_destinations.add(destination_key)
        self.vacated_sources.add(_path_key(source))
        return True


def _has_space_for_real_move(source: Path, destination: Path, file_size: int) -> bool:
    required = _copy_space_required(source, destination, file_size)
    if not required:
        return True
    free = _destination_free_space(destination)
    if free is None:
        LOG.error("Cannot determine free space for destination: %s", destination)
        return False
    if required > free:
        LOG.error(
            "NOT ENOUGH SPACE for %s: needs %s, only %s is available.",
            destination,
            _format_bytes(required),
            _format_bytes(free),
        )
        return False
    return True


def _safe_folder_component(value: str) -> str:
    value = value.strip()
    if (
        not value
        or value in {".", ".."}
        or Path(value).name != value
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
        or value.endswith((" ", "."))
    ):
        raise ValueError("New series name is not a safe Windows folder name.")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
    }
    if value.upper() in reserved:
        raise ValueError("New series name is reserved by Windows.")
    return value


def _replace_path_prefix(value: str, old_root: Path, new_root: Path) -> str | None:
    try:
        relative = Path(value).resolve(strict=False).relative_to(old_root)
    except (OSError, ValueError):
        return None
    return str(new_root / relative)


def rename_series_folder(series_folder: Path, new_name: str) -> tuple[Path, int, str]:
    """Rename a series and transactionally migrate every rollback path."""
    if not series_folder.is_dir():
        raise FileNotFoundError(f"Series folder does not exist: {series_folder}")
    new_name = _safe_folder_component(new_name)
    old_root = series_folder.resolve()
    new_root = old_root.parent / new_name
    if old_root == new_root:
        raise ValueError("The new folder name is the same as the current name.")
    if new_root.exists():
        raise FileExistsError(f"Destination folder already exists: {new_root}")

    migration_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    plans: list[dict] = []
    affected = 0

    # Preflight every history file before touching the folder.
    for history_path in old_root.rglob(HISTORY_NAME):
        try:
            records = json.loads(history_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid history file {history_path}: {exc}") from exc
        if not isinstance(records, list):
            raise ValueError(f"History root is not an array: {history_path}")
        for record in records:
            if not isinstance(record, dict):
                raise ValueError(f"Invalid record in history: {history_path}")
            changed = False
            for field in ("original_full_path", "new_full_path"):
                current = record.get(field)
                if not isinstance(current, str):
                    continue
                replacement = _replace_path_prefix(current, old_root, new_root)
                if replacement is not None and replacement != current:
                    record.setdefault(f"recorded_{field}", current)
                    record[field] = replacement
                    changed = True
            if changed:
                path_migrations = record.setdefault("path_migrations", [])
                if not isinstance(path_migrations, list):
                    raise ValueError(
                        f"Invalid path_migrations in history: {history_path}"
                    )
                path_migrations.append(
                    {
                        "timestamp": now_iso(),
                        "migration_id": migration_id,
                        "old_folder": str(old_root),
                        "new_folder": str(new_root),
                    }
                )
                affected += 1
        plans.append(
            {
                "relative": history_path.relative_to(old_root),
                "content": json.dumps(records, ensure_ascii=False, indent=2) + "\n",
                "existed": True,
            }
        )

    folder_history = old_root / FOLDER_HISTORY_NAME
    if folder_history.exists():
        try:
            migrations = json.loads(folder_history.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid folder rename history: {exc}") from exc
        if not isinstance(migrations, list):
            raise ValueError("Folder rename history root is not an array.")
    else:
        migrations = []
    migrations.append(
        {
            "timestamp": now_iso(),
            "migration_id": migration_id,
            "old_folder": str(old_root),
            "new_folder": str(new_root),
            "affected_history_records": affected,
            "status": "done",
        }
    )
    plans.append(
        {
            "relative": Path(FOLDER_HISTORY_NAME),
            "content": json.dumps(migrations, ensure_ascii=False, indent=2) + "\n",
            "existed": folder_history.exists(),
        }
    )

    # Prepare new copies and byte-for-byte backups inside the folder. They move
    # with the folder, allowing rollback even after the directory rename.
    try:
        for plan in plans:
            final = old_root / plan["relative"]
            plan["new_suffix"] = f".migrate-new-{migration_id}"
            plan["backup_suffix"] = f".migrate-backup-{migration_id}"
            prepared = final.with_name(final.name + plan["new_suffix"])
            prepared.write_text(plan["content"], encoding="utf-8")
            if plan["existed"]:
                shutil.copy2(
                    final, final.with_name(final.name + plan["backup_suffix"])
                )
    except Exception:
        for plan in plans:
            if "new_suffix" not in plan:
                continue
            final = old_root / plan["relative"]
            final.with_name(final.name + plan["new_suffix"]).unlink(missing_ok=True)
            final.with_name(final.name + plan["backup_suffix"]).unlink(missing_ok=True)
        raise

    renamed = False
    current_root = old_root
    try:
        old_root.rename(new_root)
        renamed = True
        current_root = new_root
        for plan in plans:
            final = current_root / plan["relative"]
            prepared = final.with_name(final.name + plan["new_suffix"])
            prepared.replace(final)
        for plan in plans:
            final = current_root / plan["relative"]
            final.with_name(final.name + plan["backup_suffix"]).unlink(missing_ok=True)
        LOG.info(
            "Renamed series folder: %s -> %s; migrated %d history records",
            old_root, new_root, affected,
        )
        return new_root, affected, migration_id
    except Exception:
        # Restore original history bytes before restoring the original folder.
        for plan in plans:
            final = current_root / plan["relative"]
            backup = final.with_name(final.name + plan["backup_suffix"])
            prepared = final.with_name(final.name + plan["new_suffix"])
            try:
                if plan["existed"] and backup.exists():
                    backup.replace(final)
                elif not plan["existed"]:
                    final.unlink(missing_ok=True)
                prepared.unlink(missing_ok=True)
                backup.unlink(missing_ok=True)
            except OSError:
                LOG.critical("Could not restore migration file: %s", final)
        if renamed and new_root.exists() and not old_root.exists():
            new_root.rename(old_root)
        raise


def explicit_episode_match(stem: str) -> tuple[int, int] | None:
    def numbers(match: re.Match) -> tuple[int, int]:
        return (
            int(normalize_digits(match["s"])),
            int(normalize_digits(match["e"])),
        )

    patterns = (
        r"(?i)(?<![a-z0-9])s(?P<s>\d{1,3})[ ._-]*e(?P<e>\d{1,4})(?!\d)",
        r"(?i)(?<!\d)(?P<s>\d{1,3})[ ._-]*x[ ._-]*(?P<e>\d{1,4})(?!\d)",
        # Season 4 Episode 25 / Season.4.Ep.25 / Season04E25
        r"(?i)\bseason[ ._-]*(?P<s>\d{1,3})[ ._-]*"
        r"(?:episode|ep|e)[ ._-]*(?P<e>\d{1,4})(?!\d)",
        # S4 EP25 / S04 Episode 025
        r"(?i)(?<![a-z0-9])s(?P<s>\d{1,3})[ ._-]*"
        r"(?:episode|ep)[ ._-]*(?P<e>\d{1,4})(?!\d)",
        # Episode 25 - S4 / E25.S04
        r"(?i)(?:episode|ep|e)[ ._-]*(?P<e>\d{1,4})[ ._-]+"
        r"s(?:eason)?[ ._-]*(?P<s>\d{1,3})(?!\d)",
    )
    for pattern in patterns:
        match = re.search(pattern, stem)
        if match:
            return numbers(match)

    # Common anime release formats without an E marker:
    # "Show S4 - 25 [480p]" and "Season 4 - 25".
    for pattern in (
        r"(?i)(?<![a-z0-9])s(?P<s>\d{1,3})\s*[-._ ]+\s*"
        r"(?P<e>\d{1,4})(?!\d|p\b)",
        r"(?i)\bseason[ ._-]*(?P<s>\d{1,3})\s*[-._ ]+\s*"
        r"(?P<e>\d{1,4})(?!\d|p\b)",
    ):
        anime_match = re.search(pattern, stem)
        if anime_match:
            season, episode = numbers(anime_match)
            if episode not in {360, 480, 720, 1080, 1440, 2160}:
                return season, episode

    # Persian/Arabic season + episode:
    # "فصل ۴ قسمت ۲۵" / "الموسم 4 الحلقة 25".
    localized_pair = re.search(
        r"(?:فصل|الموسم)[\s._-]*(?P<s>[0-9۰-۹٠-٩]{1,3})"
        r"[\s._-]*(?:قسمت|حلقة|الحلقة)[\s._-]*"
        r"(?P<e>[0-9۰-۹٠-٩]{1,4})",
        stem,
    )
    if localized_pair:
        return numbers(localized_pair)

    episode_patterns = (
        r"(?i)(?:episode|ep)[ ._-]*(?P<e>\d{1,4})(?!\d)",
        r"(?i)(?<![a-z0-9])e[ ._-]*(?P<e>\d{1,4})(?!\d)",
        # Versioned anime releases: "Episode 25v2" / "EP25v3".
        r"(?i)(?:episode|ep)[ ._-]*(?P<e>\d{1,4})v\d+(?!\d)",
        r"(?:قسمت|حلقة|الحلقة)[\s._-]*(?P<e>[0-9۰-۹٠-٩]{1,4})",
        # Japanese 第25話 / 25話 and Korean 25화.
        r"(?:第\s*)?(?P<e>[0-9０-９]{1,4})\s*話",
        r"(?P<e>[0-9０-９]{1,4})\s*화",
    )
    for pattern in episode_patterns:
        match = re.search(pattern, stem)
        if match:
            return 1, int(normalize_digits(match["e"]))
    return None


def normalize_digits(value: str) -> str:
    source = "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩０１２３４５６７８９"
    target = "012345678901234567890123456789"
    return value.translate(str.maketrans(source, target))


def safe_numeric_fallback(stem: str) -> int | None:
    """Accept one isolated episode number while rejecting technical metadata."""
    normalized = normalize_digits(stem)
    matches = list(re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", normalized))
    ignored = {360, 480, 720, 1080, 1440, 2160, 264, 265}
    technical_spans = [
        match.span()
        for pattern in (
            r"(?i)(?<!\d)(?:8|10|12)[ ._-]*bit\b",
            r"(?i)\b(?:aac|ac3|eac3|dts)[ ._-]*\d{1,2}\b",
        )
        for match in re.finditer(pattern, normalized)
    ]
    candidates: list[int] = []
    for match in matches:
        number = int(match.group(1))
        start, end = match.span(1)
        if number in ignored or 1900 <= number <= 2099 or not 1 <= number <= 9999:
            continue
        # Reject release/codec labels such as Group2, AAC2, AV1 and 10bit.
        if (
            (start > 0 and normalized[start - 1].isalpha())
            or (end < len(normalized) and normalized[end].isalpha())
            or any(start < tech_end and end > tech_start for tech_start, tech_end in technical_spans)
        ):
            continue
        candidates.append(number)
    if len(candidates) == 1:
        return candidates[0]
    return None


def detect_episode(path: Path) -> tuple[int, int] | None:
    stem = path.stem
    explicit = explicit_episode_match(stem)
    if explicit:
        return explicit

    # A visible season marker without a matched episode must not be recycled by
    # guessit or the lone-number fallback as an episode number.
    if re.search(
        r"(?i)(?<![a-z0-9])s(?:eason)?[ ._-]*\d{1,3}(?!\d)|"
        r"(?:فصل|الموسم)[\s._-]*[0-9۰-۹٠-٩]{1,3}",
        stem,
    ):
        return None

    if guessit is not None:
        try:
            guessed = guessit(path.name, {"type": "episode"})
            episode = guessed.get("episode")
            season = guessed.get("season", 1)
            if isinstance(episode, list):
                episode = episode[0] if len(episode) == 1 else None
            if isinstance(season, list):
                season = season[0] if len(season) == 1 else 1
            if (
                isinstance(episode, int)
                and not isinstance(episode, bool)
                and 1 <= episode <= 9999
            ):
                # GuessIt interprets names such as video_001 as Season 00 even
                # though no explicit season exists. Explicit S00E01 was already
                # handled above, so a non-positive guessed season means Season 01.
                if (
                    not isinstance(season, int)
                    or isinstance(season, bool)
                    or season <= 0
                ):
                    season = 1
                if season <= 999:
                    return season, episode
        except Exception as exc:  # Third-party parsing must not stop a batch.
            LOG.debug("guessit failed for %s: %s", path.name, exc)

    episode = safe_numeric_fallback(stem)
    return (1, episode) if episode is not None else None


def _destination_exists(path: Path, dry_run_state: DryRunState | None) -> bool:
    return (
        dry_run_state.exists(path)
        if dry_run_state is not None
        else path.exists()
    )


def unique_conflict_path(
    folder: Path,
    original_name: str,
    dry_run_state: DryRunState | None = None,
) -> Path:
    candidate = folder / original_name
    if not _destination_exists(candidate, dry_run_state):
        return candidate
    stem, suffix = Path(original_name).stem, Path(original_name).suffix
    counter = 1
    while True:
        candidate = folder / f"{stem} ({counter}){suffix}"
        if not _destination_exists(candidate, dry_run_state):
            return candidate
        counter += 1


def move_and_record(
    source: Path,
    destination: Path,
    history_folder: Path,
    file_type: str,
    status: str,
    batch_id: str,
    dry_run: bool,
    operation: str = "organize",
    dry_run_state: DryRunState | None = None,
) -> bool:
    action = "WOULD MOVE" if dry_run else "MOVE"
    if dry_run:
        state = dry_run_state or DryRunState()
        if not state.reserve(source, destination):
            return False
        LOG.info("%s: %s -> %s [%s]", action, source, destination, status)
        return True

    LOG.info("%s: %s -> %s [%s]", action, source, destination, status)
    operation_id = uuid.uuid4().hex
    try:
        # Recheck at the last possible moment. This also protects callers if a
        # destination appeared after planning but before the move.
        if destination.exists():
            LOG.error("Refusing to overwrite existing destination: %s", destination)
            return False
        size = source.stat().st_size
        if not _has_space_for_real_move(source, destination, size):
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        record = HistoryRecord(
            timestamp=now_iso(),
            original_full_path=str(source.resolve()),
            new_full_path=str(destination.resolve()),
            original_filename=source.name,
            new_filename=destination.name,
            file_size=size,
            file_type=file_type,
            status=status,
            batch_id=batch_id,
            operation=operation,
            operation_id=operation_id,
        )
        journal_details = asdict(record)
        if not append_journal(history_folder, "move-planned", journal_details):
            LOG.error("Refusing to move without a durable journal entry: %s", source)
            return False
        shutil.move(str(source), str(destination))
    except OSError as exc:
        LOG.error("Failed moving %s: %s", source, exc)
        append_journal(
            history_folder,
            "move-failed",
            {
                "operation_id": operation_id,
                "original_full_path": str(source.resolve()),
                "new_full_path": str(destination.resolve()),
                "error": str(exc),
            },
        )
        return False

    if not _move_is_verified(source, destination, size):
        LOG.error("Move verification failed: %s -> %s", source, destination)
        append_journal(history_folder, "move-verification-failed", journal_details)
        rolled_back = _rollback_move(destination, source)
        append_journal(
            history_folder,
            "move-rolled-back" if rolled_back else "move-rollback-failed",
            journal_details,
        )
        return False
    append_journal(history_folder, "move-verified", journal_details)

    if not append_history(history_folder / HISTORY_NAME, record):
        LOG.error("Move succeeded but history recording failed for %s", destination)
        append_journal(history_folder, "move-history-save-failed", journal_details)
        rolled_back = _rollback_move(destination, source)
        if rolled_back:
            LOG.warning("Reverted unrecorded move: %s", source)
        append_journal(
            history_folder,
            "move-rolled-back" if rolled_back else "move-rollback-failed",
            journal_details,
        )
        return False
    if not append_journal(history_folder, "move-done", journal_details):
        # The required rename history is already durable, so the move remains
        # recoverable even if this optional final journal phase cannot be added.
        LOG.error("Move history is safe, but final journal phase was not written.")
    return True


def organize_video(
    video: Path,
    series_name: str,
    library: Path,
    subtitles: list[Path],
    batch_id: str,
    dry_run: bool,
    operation: str = "organize",
    dry_run_state: DryRunState | None = None,
    replace_existing: bool = False,
    detected_override: tuple[int, int] | None = None,
) -> bool:
    series_folder = library / series_name
    detected = detected_override or detect_episode(video)

    if detected is None:
        target_folder = series_folder / "_Unsorted"
        target = target_folder / video.name
        if _destination_exists(target, dry_run_state):
            target_folder = series_folder / "_Conflicts"
            target = unique_conflict_path(
                target_folder, video.name, dry_run_state
            )
            status = "conflict"
        else:
            status = "unsorted"
        history_folder = series_folder
        moved = move_and_record(
            video, target, history_folder, "video", status, batch_id, dry_run,
            operation, dry_run_state
        )
        success = moved
        if moved:
            for subtitle in subtitles:
                subtitle_target = target.with_suffix(subtitle.suffix)
                if _destination_exists(subtitle_target, dry_run_state):
                    subtitle_target = unique_conflict_path(
                        series_folder / "_Conflicts",
                        subtitle.name,
                        dry_run_state,
                    )
                    subtitle_status = "conflict"
                else:
                    subtitle_status = status
                subtitle_moved = move_and_record(
                    subtitle, subtitle_target, history_folder, "subtitle",
                    subtitle_status, batch_id, dry_run, operation, dry_run_state
                )
                success = subtitle_moved and success
        return success

    season, episode = detected
    season_folder = series_folder / f"Season {season:02d}"
    clean_stem = f"{series_file_title(series_name)} - S{season:02d}E{episode:02d}"
    target = season_folder / f"{clean_stem}{video.suffix}"
    if target.resolve(strict=False) == video.resolve(strict=False):
        LOG.info("SKIP (already correctly named): %s", video)
        return True
    existing_videos: list[Path] = []
    for item in series_folder.rglob("*"):
        if (
            not item.is_file()
            or item.resolve(strict=False) == video.resolve(strict=False)
            or item.parent == series_folder
            or item.suffix.lower() not in VIDEO_EXTENSIONS
        ):
            continue
        relative_parts = item.relative_to(series_folder).parts[:-1]
        if any(
            part in {"_Unsorted", "_Conflicts"} or part.startswith(".")
            for part in relative_parts
        ):
            continue
        if detect_episode(item) == detected:
            existing_videos.append(item)
    existing_episode_media = list(existing_videos)
    for existing_video in existing_videos:
        for sibling in existing_video.parent.iterdir():
            if sibling.suffix.lower() not in SUBTITLE_EXTENSIONS:
                continue
            if (
                sibling.stem.casefold() == existing_video.stem.casefold()
                or sibling.stem.casefold().startswith(
                    existing_video.stem.casefold() + "."
                )
            ):
                existing_episode_media.append(sibling)
    existing_episode_media = sorted(
        set(existing_episode_media), key=lambda item: str(item).casefold()
    )

    if existing_videos and replace_existing:
        backup_folder = series_folder / ".replacement_backups" / batch_id
        for existing in existing_episode_media:
            kind = (
                "video"
                if existing.suffix.lower() in VIDEO_EXTENSIONS
                else "subtitle"
            )
            if not move_and_record(
                existing,
                backup_folder / existing.relative_to(series_folder),
                season_folder,
                kind,
                "done",
                batch_id,
                dry_run,
                "replace-existing",
                dry_run_state,
            ):
                return False
        if not dry_run:
            remaining = [
                item
                for item in series_folder.rglob("*")
                if item.is_file()
                and item.parent != series_folder
                and item.suffix.lower() in VIDEO_EXTENSIONS
                and not any(
                    part in {"_Unsorted", "_Conflicts"} or part.startswith(".")
                    for part in item.relative_to(series_folder).parts[:-1]
                )
                and detect_episode(item) == detected
            ]
            if remaining:
                LOG.error(
                    "Episode media appeared during replacement; refusing to install: %s",
                    remaining[0],
                )
                return False
    elif existing_videos or _destination_exists(target, dry_run_state):
        conflict_folder = series_folder / "_Conflicts"
        conflict_target = unique_conflict_path(
            conflict_folder, video.name, dry_run_state
        )
        moved = move_and_record(
            video, conflict_target, series_folder, "video", "conflict",
            batch_id, dry_run, operation, dry_run_state
        )
        success = moved
        if moved:
            for subtitle in subtitles:
                sub_target = unique_conflict_path(
                    conflict_folder, subtitle.name, dry_run_state
                )
                subtitle_moved = move_and_record(
                    subtitle, sub_target, series_folder, "subtitle", "conflict",
                    batch_id, dry_run, operation, dry_run_state
                )
                success = subtitle_moved and success
        return success

    moved = move_and_record(
        video, target, season_folder, "video", "done", batch_id, dry_run,
        operation, dry_run_state
    )
    success = moved
    if moved:
        for subtitle in subtitles:
            subtitle_target = season_folder / f"{clean_stem}{subtitle.suffix}"
            if _destination_exists(subtitle_target, dry_run_state):
                subtitle_target = unique_conflict_path(
                    series_folder / "_Conflicts",
                    subtitle.name,
                    dry_run_state,
                )
                subtitle_moved = move_and_record(
                    subtitle, subtitle_target, series_folder, "subtitle", "conflict",
                    batch_id, dry_run, operation, dry_run_state
                )
            else:
                subtitle_moved = move_and_record(
                    subtitle, subtitle_target, season_folder, "subtitle", "done",
                    batch_id, dry_run, operation, dry_run_state
                )
            success = subtitle_moved and success
    return success


def _strict_json_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON file {path}: {exc}") from exc
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"JSON root must be an array of objects: {path}")
    return value


def _series_batch_records(series_folder: Path) -> dict[str, list[dict]]:
    batches: dict[str, list[dict]] = {}
    for path in history_files(series_folder):
        for record in load_history(path):
            batch_id = record.get("batch_id")
            if batch_id:
                batches.setdefault(str(batch_id), []).append(record)
    return batches


def _revision_status(records: list[dict]) -> str:
    statuses = {record.get("status") for record in records}
    if statuses == {"undone"}:
        return "undone"
    if "undone" in statuses:
        return "partial"
    return "applied"


def sync_sort_revisions(
    series_folder: Path, operation_overrides: dict[str, str] | None = None
) -> list[dict]:
    """Discover old batches and maintain stable human-friendly revision numbers."""
    operation_overrides = operation_overrides or {}
    revision_path = series_folder / REVISION_HISTORY_NAME
    revisions = _strict_json_list(revision_path)
    batches = _series_batch_records(series_folder)
    by_batch = {str(item.get("batch_id")): item for item in revisions}
    next_number = max((int(item.get("revision", 0)) for item in revisions), default=0) + 1

    ordered_batches = sorted(
        batches.items(),
        key=lambda item: min(
            (str(record.get("timestamp", "")) for record in item[1]),
            default="",
        ),
    )
    for batch_id, records in ordered_batches:
        operation = operation_overrides.get(
            batch_id, str(records[0].get("operation", "organize"))
        )
        if batch_id not in by_batch:
            entry = {
                "revision": next_number,
                "batch_id": batch_id,
                "timestamp": min(
                    (str(record.get("timestamp", "")) for record in records),
                    default=now_iso(),
                ),
                "operation": operation,
                "file_count": len(records),
                "status": _revision_status(records),
            }
            revisions.append(entry)
            by_batch[batch_id] = entry
            next_number += 1
        else:
            entry = by_batch[batch_id]
            entry["operation"] = operation
            entry["file_count"] = len(records)
            entry["status"] = _revision_status(records)

    revisions.sort(key=lambda item: int(item.get("revision", 0)))
    if series_folder.exists() and (revisions or revision_path.exists()):
        if not save_history(revision_path, revisions):
            raise OSError(f"Could not save sort revisions: {revision_path}")
    return revisions


def resort_existing(series_folder: Path, dry_run: bool = False) -> int:
    """Explicitly rename already-organized Season files to the current title."""
    if not series_folder.is_dir():
        LOG.error("Series folder does not exist: %s", series_folder)
        return 2
    batch_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    LOG.info("Resort Batch ID: %s", batch_id)
    videos_found = 0
    failures = 0
    dry_run_state = DryRunState() if dry_run else None
    candidate_folders = []
    for folder in series_folder.iterdir():
        if not folder.is_dir():
            continue
        # Support folders produced by older versions and common manual layouts.
        if (
            re.fullmatch(r"(?i)Season[\s._-]*\d{1,3}", folder.name)
            or re.fullmatch(r"(?i)S\d{1,3}", folder.name)
            or re.fullmatch(r"(?:فصل|الموسم)[\s._-]*[0-9۰-۹٠-٩]{1,3}", folder.name)
            or folder.name.casefold() == "_unsorted"
        ):
            candidate_folders.append(folder)

    for season_folder in sorted(candidate_folders, key=lambda p: p.name.casefold()):
        videos = sorted(
            (
                path for path in season_folder.rglob("*")
                if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
            ),
            key=lambda path: path.name.casefold(),
        )
        subtitles: dict[str, list[Path]] = {}
        for path in season_folder.rglob("*"):
            if path.is_file() and path.suffix.lower() in SUBTITLE_EXTENSIONS:
                key = f"{path.parent.resolve()}::{path.stem.casefold()}"
                subtitles.setdefault(key, []).append(path)
        for video in videos:
            videos_found += 1
            subtitle_key = f"{video.parent.resolve()}::{video.stem.casefold()}"
            if not organize_video(
                video,
                series_folder.name,
                series_folder.parent,
                subtitles.get(subtitle_key, []),
                batch_id,
                dry_run,
                operation="resort-existing",
                dry_run_state=dry_run_state,
            ):
                failures += 1
    if not videos_found:
        LOG.error(
            "No existing videos found in Season, Sxx, localized season, "
            "or _Unsorted folders under %s",
            series_folder,
        )
        return 1
    if not dry_run:
        revisions = sync_sort_revisions(
            series_folder, {batch_id: "resort-existing"}
        )
        current = next(
            (item for item in revisions if item["batch_id"] == batch_id), None
        )
        if current:
            LOG.info("Sort revision: #%s", current["revision"])
        else:
            LOG.info("No filenames needed changing; no revision was created.")
    if failures:
        LOG.error("Resort completed with %d failed video operation(s).", failures)
        return 1
    return 0


def fix_episode_metadata(series_folder: Path, dry_run: bool = False) -> int:
    """Manually align episode NFO/artwork names with videos in one series."""
    if not series_folder.is_dir():
        LOG.error("Series folder does not exist: %s", series_folder)
        return 2
    batch_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    LOG.info("Metadata Batch ID: %s", batch_id)
    dry_run_state = DryRunState() if dry_run else None
    videos_by_episode: dict[tuple[Path, int, int], list[Path]] = {}
    ignored_folders = {"_conflicts", "_unsorted"}

    for path in series_folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        try:
            relative_parts = path.relative_to(series_folder).parts[:-1]
        except ValueError:
            continue
        if any(part.casefold() in ignored_folders for part in relative_parts):
            continue
        detected = detect_episode(path)
        if detected is None:
            continue
        season, episode = detected
        key = (path.parent.resolve(), season, episode)
        videos_by_episode.setdefault(key, []).append(path)

    candidates = [
        path
        for path in series_folder.rglob("*")
        if path.is_file()
        and path.suffix.lower() in NFO_EXTENSIONS | ARTWORK_EXTENSIONS
        and not any(
            part.casefold() in ignored_folders
            for part in path.relative_to(series_folder).parts[:-1]
        )
    ]
    changed = failures = ambiguous = 0
    for sidecar in sorted(candidates, key=lambda path: str(path).casefold()):
        detected = detect_episode(sidecar)
        if detected is None:
            continue
        season, episode = detected
        matches = videos_by_episode.get(
            (sidecar.parent.resolve(), season, episode), []
        )
        if len(matches) != 1:
            if len(matches) > 1:
                LOG.warning(
                    "SKIP (ambiguous episode metadata): %s matches %d videos",
                    sidecar,
                    len(matches),
                )
                ambiguous += 1
            continue
        video = matches[0]
        if sidecar.suffix.lower() in NFO_EXTENSIONS:
            target = video.with_suffix(sidecar.suffix)
            file_type = "metadata"
        else:
            target = video.parent / f"{video.stem}-thumb{sidecar.suffix}"
            file_type = "artwork"
        if target.resolve(strict=False) == sidecar.resolve(strict=False):
            LOG.info("SKIP (metadata already correctly named): %s", sidecar)
            continue

        if _destination_exists(target, dry_run_state):
            history_folder = series_folder
            target = unique_conflict_path(
                series_folder / "_Conflicts",
                sidecar.name,
                dry_run_state,
            )
            status = "conflict"
        else:
            history_folder = video.parent
            status = "done"
        if move_and_record(
            sidecar,
            target,
            history_folder,
            file_type,
            status,
            batch_id,
            dry_run,
            operation="fix-metadata",
            dry_run_state=dry_run_state,
        ):
            changed += 1
        else:
            failures += 1

    if dry_run:
        LOG.info(
            "Metadata dry run complete: %d change(s), %d ambiguous, %d failed.",
            changed,
            ambiguous,
            failures,
        )
    elif changed:
        revisions = sync_sort_revisions(
            series_folder, {batch_id: "fix-metadata"}
        )
        current = next(
            (item for item in revisions if item["batch_id"] == batch_id), None
        )
        if current:
            LOG.info("Metadata sort revision: #%s", current["revision"])
    else:
        LOG.info("No episode metadata filenames needed changing.")
    if failures or ambiguous:
        LOG.error(
            "Metadata rename completed with %d failed and %d ambiguous file(s).",
            failures,
            ambiguous,
        )
        return 1
    return 0


def run_organizer(
    series_folder: Path,
    library: Path | None = None,
    dry_run: bool = False,
    replace_episodes: set[tuple[int, int]] | None = None,
    episode_overrides: dict[str, tuple[int, int]] | None = None,
) -> int:
    """Organize exactly one selected series folder.

    The selected folder's name is the trusted series title. Deliberately not
    scanning its sibling folders prevents an accidental library-wide run.
    """
    if not series_folder.is_dir():
        LOG.error("Series folder does not exist: %s", series_folder)
        return 2
    # By default the selected folder is already the final Jellyfin series
    # folder. Its parent is therefore the Jellyfin shows library.
    if library is None:
        library = series_folder.parent
    elif series_folder.resolve() == library.resolve():
        # Accept the common interpretation that --library names the existing
        # series folder itself, rather than rejecting a harmless duplicate.
        library = series_folder.parent

    batch_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    LOG.info("Batch ID: %s", batch_id)
    videos = sorted(
        (
            p for p in series_folder.iterdir()
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
        ),
        key=lambda p: p.name.lower(),
    )
    subtitles_by_stem: dict[str, list[Path]] = {}
    for item in series_folder.iterdir():
        if item.is_file() and item.suffix.lower() in SUBTITLE_EXTENSIONS:
            subtitles_by_stem.setdefault(item.stem.casefold(), []).append(item)

    replacements = replace_episodes or set()
    overrides = {
        filename.casefold(): detected
        for filename, detected in (episode_overrides or {}).items()
    }
    failures = 0
    dry_run_state = DryRunState() if dry_run else None
    for video in videos:
        matching_subtitles = subtitles_by_stem.pop(video.stem.casefold(), [])
        detected_override = overrides.get(video.name.casefold())
        detected = detected_override or detect_episode(video)
        if not organize_video(
            video, series_folder.name, library, matching_subtitles,
            batch_id, dry_run, dry_run_state=dry_run_state,
            replace_existing=bool(detected and detected in replacements),
            detected_override=detected_override,
        ):
            failures += 1
            if replacements:
                break
    if failures and replacements and not dry_run:
        # A replacement is one logical transaction: restore both previously
        # archived media and any new episodes moved earlier in this batch.
        undo_records(list(history_files(library / series_folder.name)), batch_id)
    if not videos:
        LOG.warning("No supported video files found directly inside %s", series_folder)
    elif dry_run:
        LOG.info("Dry run complete; no files or history were changed.")
    else:
        series_destination = library / series_folder.name
        revisions = sync_sort_revisions(
            series_destination, {batch_id: "sort-new"}
        )
        current = next(
            (item for item in revisions if item["batch_id"] == batch_id), None
        )
        if current:
            LOG.info("Sort revision: #%s", current["revision"])
        LOG.info("Batch complete: %s", batch_id)
    if failures:
        LOG.error("Batch completed with %d failed video operation(s).", failures)
        return 1
    return 0


def history_files(root: Path) -> Iterable[Path]:
    if root.is_file() and root.name == HISTORY_NAME:
        yield root
    elif root.is_dir():
        yield from root.rglob(HISTORY_NAME)


def undo_records(files: list[Path], batch_id: str | None = None) -> tuple[int, int]:
    candidates: list[tuple[str, Path, int, dict]] = []
    histories: dict[Path, list[dict]] = {}
    for history_path in files:
        records = load_history(history_path)
        histories[history_path] = records
        for index, record in enumerate(records):
            if record.get("status") == "undone":
                continue
            if batch_id is not None and record.get("batch_id") != batch_id:
                continue
            candidates.append((record.get("timestamp", ""), history_path, index, record))

    # Reverse move order, important for paired files and nested paths.
    candidates.sort(key=lambda item: item[0], reverse=True)
    restored = skipped = 0
    for _, history_path, index, record in candidates:
        current = Path(record["new_full_path"])
        original = Path(record["original_full_path"])
        if original.exists():
            LOG.warning("SKIP (original exists): %s", original)
            skipped += 1
            continue
        if not current.exists():
            LOG.warning("SKIP (organized file missing): %s", current)
            skipped += 1
            continue
        expected_size = record.get("file_size")
        try:
            actual_size = current.stat().st_size
        except OSError as exc:
            LOG.warning("SKIP (cannot inspect %s): %s", current, exc)
            skipped += 1
            continue
        if isinstance(expected_size, int) and actual_size != expected_size:
            LOG.warning(
                "SKIP (size changed): %s expected %s, found %s",
                current, expected_size, actual_size,
            )
            skipped += 1
            continue
        action_id = uuid.uuid4().hex
        journal_details = {
            "operation_id": action_id,
            "related_operation_id": record.get("operation_id", ""),
            "history_path": str(history_path.resolve()),
            "history_index": index,
            "batch_id": record.get("batch_id", ""),
            "original_full_path": str(original.resolve()),
            "new_full_path": str(current.resolve()),
            "file_size": actual_size,
            "action": "undo",
        }
        if not append_journal(history_path.parent, "undo-planned", journal_details):
            LOG.error("Refusing to undo without a durable journal entry: %s", current)
            skipped += 1
            continue
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(current), str(original))
        except OSError as exc:
            LOG.error("Failed restoring %s: %s", current, exc)
            append_journal(
                history_path.parent,
                "undo-move-failed",
                {**journal_details, "error": str(exc)},
            )
            skipped += 1
            continue
        if not _move_is_verified(current, original, actual_size):
            LOG.error("Undo verification failed: %s -> %s", current, original)
            append_journal(
                history_path.parent, "undo-verification-failed", journal_details
            )
            rolled_back = _rollback_move(original, current)
            append_journal(
                history_path.parent,
                "undo-rolled-back" if rolled_back else "undo-rollback-failed",
                journal_details,
            )
            skipped += 1
            continue

        previous_record = dict(histories[history_path][index])
        histories[history_path][index]["previous_status"] = record.get(
            "status", "done"
        )
        histories[history_path][index]["status"] = "undone"
        histories[history_path][index]["undone_timestamp"] = now_iso()
        if not save_history(history_path, histories[history_path]):
            histories[history_path][index] = previous_record
            LOG.error("Undo history save failed; restoring organized location.")
            append_journal(
                history_path.parent, "undo-history-save-failed", journal_details
            )
            rolled_back = _rollback_move(original, current)
            append_journal(
                history_path.parent,
                "undo-rolled-back" if rolled_back else "undo-rollback-failed",
                journal_details,
            )
            skipped += 1
            continue
        append_journal(history_path.parent, "undo-done", journal_details)
        restored += 1
        LOG.info("RESTORED: %s -> %s", current, original)
    return restored, skipped


def redo_records(files: list[Path], batch_id: str) -> tuple[int, int]:
    candidates: list[tuple[str, Path, int, dict]] = []
    histories: dict[Path, list[dict]] = {}
    for history_path in files:
        records = load_history(history_path)
        histories[history_path] = records
        for index, record in enumerate(records):
            if (
                record.get("status") == "undone"
                and record.get("batch_id") == batch_id
            ):
                candidates.append(
                    (record.get("timestamp", ""), history_path, index, record)
                )

    candidates.sort(key=lambda item: item[0])
    restored = skipped = 0
    for _, history_path, index, record in candidates:
        original = Path(record["original_full_path"])
        target = Path(record["new_full_path"])
        if target.exists():
            LOG.warning("SKIP (destination exists): %s", target)
            skipped += 1
            continue
        if not original.exists():
            LOG.warning("SKIP (original file missing): %s", original)
            skipped += 1
            continue
        expected_size = record.get("file_size")
        try:
            actual_size = original.stat().st_size
        except OSError as exc:
            LOG.warning("SKIP (cannot inspect %s): %s", original, exc)
            skipped += 1
            continue
        if isinstance(expected_size, int) and actual_size != expected_size:
            LOG.warning("SKIP (size changed): %s", original)
            skipped += 1
            continue
        action_id = uuid.uuid4().hex
        journal_details = {
            "operation_id": action_id,
            "related_operation_id": record.get("operation_id", ""),
            "history_path": str(history_path.resolve()),
            "history_index": index,
            "batch_id": batch_id,
            "original_full_path": str(original.resolve()),
            "new_full_path": str(target.resolve()),
            "file_size": actual_size,
            "action": "redo",
        }
        if not append_journal(history_path.parent, "redo-planned", journal_details):
            LOG.error("Refusing to redo without a durable journal entry: %s", original)
            skipped += 1
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(original), str(target))
        except OSError as exc:
            LOG.error("Failed reapplying %s: %s", original, exc)
            append_journal(
                history_path.parent,
                "redo-move-failed",
                {**journal_details, "error": str(exc)},
            )
            skipped += 1
            continue
        if not _move_is_verified(original, target, actual_size):
            LOG.error("Redo verification failed: %s -> %s", original, target)
            append_journal(
                history_path.parent, "redo-verification-failed", journal_details
            )
            rolled_back = _rollback_move(target, original)
            append_journal(
                history_path.parent,
                "redo-rolled-back" if rolled_back else "redo-rollback-failed",
                journal_details,
            )
            skipped += 1
            continue

        previous_record = dict(histories[history_path][index])
        histories[history_path][index]["status"] = record.get(
            "previous_status", "done"
        )
        histories[history_path][index]["redone_timestamp"] = now_iso()
        if not save_history(history_path, histories[history_path]):
            histories[history_path][index] = previous_record
            LOG.error("Redo history save failed; restoring original location.")
            append_journal(
                history_path.parent, "redo-history-save-failed", journal_details
            )
            rolled_back = _rollback_move(target, original)
            append_journal(
                history_path.parent,
                "redo-rolled-back" if rolled_back else "redo-rollback-failed",
                journal_details,
            )
            skipped += 1
            continue
        append_journal(history_path.parent, "redo-done", journal_details)
        restored += 1
        LOG.info("REAPPLIED: %s -> %s", original, target)
    return restored, skipped


def _read_journal(path: Path) -> list[dict]:
    events: list[dict] = []
    try:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError(f"line {line_number} is not an object")
            events.append(event)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Invalid operation journal {path}: {exc}") from exc
    return events


def _journal_operations(path: Path) -> list[dict]:
    """Merge each operation's journal phases while preserving first-seen order."""
    merged: dict[str, dict] = {}
    order: list[str] = []
    for event in _read_journal(path):
        operation_id = event.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id:
            continue
        if operation_id not in merged:
            merged[operation_id] = {}
            order.append(operation_id)
        merged[operation_id].update(event)
    return [merged[operation_id] for operation_id in order]


def _file_matches(path: Path, expected_size: int) -> bool:
    try:
        return path.is_file() and path.stat().st_size == expected_size
    except OSError:
        return False


def _find_recovery_history_record(
    records: list[dict], details: dict
) -> int | None:
    related_id = details.get("related_operation_id")
    if related_id:
        for index, record in enumerate(records):
            if record.get("operation_id") == related_id:
                return index

    expected_index = details.get("history_index")
    if isinstance(expected_index, int) and 0 <= expected_index < len(records):
        record = records[expected_index]
        if (
            record.get("batch_id") == details.get("batch_id")
            and record.get("original_full_path")
            == details.get("original_full_path")
            and record.get("new_full_path") == details.get("new_full_path")
        ):
            return expected_index

    for index, record in enumerate(records):
        if (
            record.get("batch_id") == details.get("batch_id")
            and record.get("original_full_path")
            == details.get("original_full_path")
            and record.get("new_full_path") == details.get("new_full_path")
        ):
            return index
    return None


def _recover_move_history(journal_folder: Path, details: dict) -> bool:
    history_path = journal_folder / HISTORY_NAME
    try:
        records = _strict_json_list(history_path)
    except ValueError as exc:
        LOG.error("%s", exc)
        return False
    operation_id = str(details.get("operation_id", ""))
    if any(record.get("operation_id") == operation_id for record in records):
        return True
    required = {
        "timestamp",
        "original_full_path",
        "new_full_path",
        "original_filename",
        "new_filename",
        "file_size",
        "file_type",
        "status",
        "batch_id",
    }
    if not required.issubset(details):
        LOG.error("Journal operation %s lacks move history fields.", operation_id)
        return False
    record = HistoryRecord(
        timestamp=str(details["timestamp"]),
        original_full_path=str(details["original_full_path"]),
        new_full_path=str(details["new_full_path"]),
        original_filename=str(details["original_filename"]),
        new_filename=str(details["new_filename"]),
        file_size=int(details["file_size"]),
        file_type=str(details["file_type"]),
        status=str(details["status"]),
        batch_id=str(details["batch_id"]),
        operation=str(details.get("operation", "organize")),
        operation_id=operation_id,
    )
    records.append(asdict(record))
    return save_history(history_path, records)


def _recover_undo_or_redo_history(
    details: dict, action: str, series_folder: Path
) -> bool:
    history_value = details.get("history_path")
    if not isinstance(history_value, str) or not history_value:
        LOG.error("Journal operation lacks its history path.")
        return False
    history_path = Path(history_value).resolve(strict=False)
    try:
        history_path.relative_to(series_folder.resolve())
    except ValueError:
        LOG.error("Journal history path is outside the selected series: %s", history_path)
        return False
    try:
        records = _strict_json_list(history_path)
    except ValueError as exc:
        LOG.error("%s", exc)
        return False
    index = _find_recovery_history_record(records, details)
    if index is None:
        LOG.error("Could not match journal operation to %s", history_path)
        return False
    record = records[index]
    if action == "undo":
        if record.get("status") != "undone":
            record["previous_status"] = record.get("status", "done")
            record["status"] = "undone"
            record["undone_timestamp"] = now_iso()
    else:
        if record.get("status") == "undone":
            record["status"] = record.get("previous_status", "done")
            record["redone_timestamp"] = now_iso()
    return save_history(history_path, records)


def recover_folder(series_folder: Path) -> int:
    """Manually reconcile incomplete journaled operations in one series folder."""
    if not series_folder.is_dir():
        LOG.error("Series folder does not exist: %s", series_folder)
        return 2
    terminal_phases = {
        "move-done",
        "move-rolled-back",
        "undo-done",
        "undo-rolled-back",
        "redo-done",
        "redo-rolled-back",
        "move-recovered-done",
        "move-recovered-no-change",
        "undo-recovered-done",
        "undo-recovered-no-change",
        "redo-recovered-done",
        "redo-recovered-no-change",
    }
    reviewed = repaired = safe_no_change = unresolved = 0
    journal_paths = sorted(series_folder.rglob(JOURNAL_NAME))
    for journal_path in journal_paths:
        try:
            operations = _journal_operations(journal_path)
        except ValueError as exc:
            LOG.error("%s", exc)
            unresolved += 1
            continue
        for details in operations:
            phase = str(details.get("phase", ""))
            if phase in terminal_phases:
                continue
            reviewed += 1
            action = str(details.get("action", ""))
            if not action:
                action = phase.partition("-")[0]
            if action not in {"move", "undo", "redo"}:
                LOG.warning(
                    "UNRESOLVED journal operation %s: unknown action %s",
                    details.get("operation_id", ""),
                    action,
                )
                unresolved += 1
                continue
            expected_size = details.get("file_size")
            if (
                not isinstance(expected_size, int)
                or isinstance(expected_size, bool)
                or expected_size < 0
            ):
                LOG.warning(
                    "UNRESOLVED journal operation %s: invalid expected size",
                    details.get("operation_id", ""),
                )
                unresolved += 1
                continue
            original_value = details.get("original_full_path")
            new_value = details.get("new_full_path")
            if not isinstance(original_value, str) or not isinstance(new_value, str):
                LOG.warning("UNRESOLVED journal operation: missing file paths")
                unresolved += 1
                continue
            original = Path(original_value)
            new = Path(new_value)
            original_ok = _file_matches(original, expected_size)
            new_ok = _file_matches(new, expected_size)
            original_exists = original.exists()
            new_exists = new.exists()

            desired_ok = (
                new_ok and not original_exists
                if action in {"move", "redo"}
                else original_ok and not new_exists
            )
            starting_ok = (
                original_ok and not new_exists
                if action in {"move", "redo"}
                else new_ok and not original_exists
            )
            if desired_ok:
                if action == "move":
                    history_ok = _recover_move_history(journal_path.parent, details)
                else:
                    history_ok = _recover_undo_or_redo_history(
                        details, action, series_folder
                    )
                if not history_ok:
                    unresolved += 1
                    continue
                if not append_journal(
                    journal_path.parent,
                    f"{action}-recovered-done",
                    details,
                ):
                    unresolved += 1
                    continue
                repaired += 1
                LOG.info(
                    "RECOVERED %s operation %s",
                    action,
                    details.get("operation_id", ""),
                )
            elif starting_ok:
                if not append_journal(
                    journal_path.parent,
                    f"{action}-recovered-no-change",
                    details,
                ):
                    unresolved += 1
                    continue
                safe_no_change += 1
                LOG.info(
                    "NO CHANGE needed for %s operation %s",
                    action,
                    details.get("operation_id", ""),
                )
            else:
                LOG.warning(
                    "UNRESOLVED %s operation %s: expected exactly one verified "
                    "copy at either %s or %s",
                    action,
                    details.get("operation_id", ""),
                    original,
                    new,
                )
                unresolved += 1
    if repaired:
        try:
            sync_sort_revisions(series_folder)
        except (OSError, ValueError) as exc:
            LOG.error("Recovered files, but revision sync failed: %s", exc)
            unresolved += 1
    LOG.info(
        "Recovery complete for %s: %d incomplete reviewed, %d repaired, "
        "%d already safe, %d unresolved",
        series_folder,
        reviewed,
        repaired,
        safe_no_change,
        unresolved,
    )
    return 1 if unresolved else 0


def sort_history(series_folder: Path) -> int:
    if not series_folder.is_dir():
        LOG.error("Series folder does not exist: %s", series_folder)
        return 2
    revisions = sync_sort_revisions(series_folder)
    if not revisions:
        LOG.warning("No sort revisions found.")
        return 1
    for item in revisions:
        LOG.info(
            "#%s | %s | %s | %s files",
            item["revision"], item["status"], item["operation"], item["file_count"],
        )
    return 0


def _batch_state_counts(
    files: Iterable[Path], batch_id: str | None = None
) -> tuple[int, int]:
    """Return active and undone record counts for an optional batch."""
    active = undone = 0
    for history_path in files:
        for record in load_history(history_path):
            if batch_id is not None and record.get("batch_id") != batch_id:
                continue
            if record.get("status") == "undone":
                undone += 1
            else:
                active += 1
    return active, undone


def change_sort_revision(
    series_folder: Path, direction: str, revision: int | None = None
) -> int:
    if not series_folder.is_dir():
        LOG.error("Series folder does not exist: %s", series_folder)
        return 2
    revisions = sync_sort_revisions(series_folder)
    if revision is not None:
        selected = next(
            (item for item in revisions if item["revision"] == revision), None
        )
    elif direction == "back":
        active = [item for item in revisions if item["status"] != "undone"]
        selected = max(active, key=lambda item: item["revision"], default=None)
    else:
        undone = [
            item for item in revisions
            if item["status"] in {"undone", "partial"}
        ]
        selected = min(undone, key=lambda item: item["revision"], default=None)
    if not selected:
        LOG.warning("No revision is available to move %s.", direction)
        return 1

    files = list(history_files(series_folder))
    batch_id = selected["batch_id"]
    active_count, undone_count = _batch_state_counts(files, batch_id)
    if direction == "back":
        expected = active_count
        if expected == 0:
            LOG.warning("Sort revision #%s is already undone.", selected["revision"])
            return 1
        moved, skipped = undo_records(files, batch_id)
    else:
        expected = undone_count
        if expected == 0:
            LOG.warning("Sort revision #%s is already applied.", selected["revision"])
            return 1
        moved, skipped = redo_records(files, batch_id)
    sync_sort_revisions(series_folder)
    LOG.info(
        "Sort %s revision #%s: %d of %d moved, %d skipped",
        direction, selected["revision"], moved, expected, skipped,
    )
    return 0 if moved == expected and skipped == 0 else 1


def undo_batch(library: Path, batch_id: str) -> int:
    if not library.is_dir():
        LOG.error("Library does not exist: %s", library)
        return 2
    files = list(history_files(library))
    active, undone = _batch_state_counts(files, batch_id)
    if active == 0:
        if undone:
            LOG.warning("Batch %s is already undone.", batch_id)
        else:
            LOG.warning("Batch %s was not found.", batch_id)
        return 1
    restored, skipped = undo_records(files, batch_id)
    LOG.info(
        "Undo batch %s: %d of %d restored, %d skipped",
        batch_id, restored, active, skipped,
    )
    return 0 if restored == active and skipped == 0 else 1


def undo_last(library: Path) -> int:
    if not library.is_dir():
        LOG.error("Library does not exist: %s", library)
        return 2
    files = list(history_files(library))
    batches: dict[str, str] = {}
    for history_path in files:
        for record in load_history(history_path):
            if record.get("status") != "undone" and record.get("batch_id"):
                batch = record["batch_id"]
                batches[batch] = max(batches.get(batch, ""), record.get("timestamp", ""))
    if not batches:
        LOG.warning("No active batch found to undo.")
        return 1
    latest = max(batches, key=batches.get)
    LOG.info("Latest batch: %s", latest)
    expected, _ = _batch_state_counts(files, latest)
    restored, skipped = undo_records(files, latest)
    LOG.info(
        "Undo complete: %d of %d restored, %d skipped",
        restored, expected, skipped,
    )
    return 0 if restored == expected and skipped == 0 else 1


def undo_folder(folder: Path) -> int:
    history_path = folder / HISTORY_NAME
    if not history_path.is_file():
        LOG.error("No %s found in %s", HISTORY_NAME, folder)
        return 2
    active, _ = _batch_state_counts([history_path])
    if active == 0:
        LOG.warning("No active records found in %s", history_path)
        return 1
    restored, skipped = undo_records([history_path])
    LOG.info(
        "Folder undo: %d of %d restored, %d skipped",
        restored, active, skipped,
    )
    return 0 if restored == active and skipped == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Organize TV episodes into a Jellyfin-compatible library."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "dry-run"):
        sub = subparsers.add_parser(command, help=f"{command} episode organization")
        sub.add_argument(
            "--series-folder",
            required=True,
            type=Path,
            help="one folder named after the series, containing its downloaded files",
        )
        sub.add_argument(
            "--library",
            type=Path,
            help=argparse.SUPPRESS,  # Legacy override; normally inferred.
        )
        sub.add_argument(
            "--replace-episode",
            action="append",
            default=[],
            metavar="S01E02",
            help="archive and replace this already-existing episode (repeatable)",
        )
        sub.add_argument(
            "--episode-override",
            action="append",
            default=[],
            nargs=2,
            metavar=("FILENAME", "S01E02"),
            help="assign a detected episode to one downloaded filename (repeatable)",
        )

    last = subparsers.add_parser("undo-last", help="undo the newest active batch")
    last.add_argument("--library", required=True, type=Path)

    batch = subparsers.add_parser("undo-batch", help="undo a specified batch")
    batch.add_argument("batch_id")
    batch.add_argument("--library", required=True, type=Path)

    folder = subparsers.add_parser("undo-folder", help="undo records in one folder")
    folder.add_argument("folder_path", type=Path)

    rename = subparsers.add_parser(
        "rename-folder",
        help="rename one series folder and migrate rollback history paths",
    )
    rename.add_argument("folder_path", type=Path)
    rename.add_argument("new_name")

    resort = subparsers.add_parser(
        "resort-existing", help="rename already sorted episodes to match the folder"
    )
    resort.add_argument("folder_path", type=Path)
    resort.add_argument("--dry-run", action="store_true")

    recover = subparsers.add_parser(
        "recover-folder",
        help="manually reconcile incomplete operations in one series folder",
    )
    recover.add_argument("folder_path", type=Path)

    metadata = subparsers.add_parser(
        "fix-metadata",
        help="rename episode NFO and artwork files in one series folder",
    )
    metadata.add_argument("folder_path", type=Path)
    metadata.add_argument("--dry-run", action="store_true")

    revisions = subparsers.add_parser(
        "sort-history", help="show numbered sort revisions for one series"
    )
    revisions.add_argument("folder_path", type=Path)

    for command in ("sort-back", "sort-forward"):
        revision = subparsers.add_parser(command)
        revision.add_argument("folder_path", type=Path)
        revision.add_argument("--revision", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        if args.command in {"run", "dry-run"}:
            replacements: set[tuple[int, int]] = set()
            for marker in args.replace_episode:
                match = re.fullmatch(
                    r"(?i)S(\d{1,3})E(\d{1,4})", str(marker).strip()
                )
                if not match or int(match.group(1)) < 1 or int(match.group(2)) < 1:
                    raise ValueError(
                        f"Invalid replacement episode marker: {marker}"
                    )
                replacements.add((int(match.group(1)), int(match.group(2))))
            overrides: dict[str, tuple[int, int]] = {}
            for filename, marker in args.episode_override:
                if Path(filename).name != filename or filename in {"", ".", ".."}:
                    raise ValueError(
                        f"Invalid episode override filename: {filename}"
                    )
                match = re.fullmatch(
                    r"(?i)S(\d{1,3})E(\d{1,4})", str(marker).strip()
                )
                if not match or int(match.group(1)) < 1 or int(match.group(2)) < 1:
                    raise ValueError(f"Invalid episode override marker: {marker}")
                key = filename.casefold()
                detected = (int(match.group(1)), int(match.group(2)))
                if key in overrides and overrides[key] != detected:
                    raise ValueError(
                        f"Conflicting episode overrides for filename: {filename}"
                    )
                overrides[key] = detected
            return run_organizer(
                args.series_folder.expanduser(),
                args.library.expanduser() if args.library else None,
                dry_run=args.command == "dry-run",
                replace_episodes=replacements,
                episode_overrides=overrides,
            )
        if args.command == "undo-last":
            return undo_last(args.library.expanduser())
        if args.command == "undo-batch":
            return undo_batch(args.library.expanduser(), args.batch_id)
        if args.command == "undo-folder":
            return undo_folder(args.folder_path.expanduser())
        if args.command == "rename-folder":
            new_path, affected, migration_id = rename_series_folder(
                args.folder_path.expanduser(), args.new_name
            )
            LOG.info("New folder: %s", new_path)
            LOG.info("Migrated history records: %d", affected)
            LOG.info("Folder migration ID: %s", migration_id)
            return 0
        if args.command == "resort-existing":
            return resort_existing(args.folder_path.expanduser(), args.dry_run)
        if args.command == "recover-folder":
            return recover_folder(args.folder_path.expanduser())
        if args.command == "fix-metadata":
            return fix_episode_metadata(
                args.folder_path.expanduser(), args.dry_run
            )
        if args.command == "sort-history":
            return sort_history(args.folder_path.expanduser())
        if args.command in {"sort-back", "sort-forward"}:
            return change_sort_revision(
                args.folder_path.expanduser(),
                "back" if args.command == "sort-back" else "forward",
                args.revision,
            )
    except KeyboardInterrupt:
        LOG.warning("Cancelled.")
        return 130
    except OSError as exc:
        LOG.error("File-system error: %s", exc)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
