#!/usr/bin/env python3
"""Safely organize downloaded TV episodes into a Jellyfin library."""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

try:
    from guessit import guessit
except ImportError:  # The regex detector remains fully usable without guessit.
    guessit = None


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".webm", ".m4v"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".vtt"}
HISTORY_NAME = ".rename_history.json"
FOLDER_HISTORY_NAME = ".folder_rename_history.json"
REVISION_HISTORY_NAME = ".sort_revisions.json"
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def series_file_title(folder_name: str) -> str:
    """Remove Jellyfin provider tags from episode filenames, not the folder."""
    title = re.sub(
        r"\s*\[(?:imdbid|tmdbid|tvdbid)-[^\]]+\]\s*",
        " ",
        folder_name,
        flags=re.IGNORECASE,
    )
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
    """Accept one isolated number, rejecting common years and video qualities."""
    normalized = normalize_digits(stem)
    numbers = [int(n) for n in re.findall(r"(?<!\d)(\d{1,4})(?!\d)", normalized)]
    ignored = {360, 480, 720, 1080, 1440, 2160, 264, 265}
    candidates = [n for n in numbers if n not in ignored and not 1900 <= n <= 2099]
    if len(candidates) == 1 and 1 <= candidates[0] <= 9999:
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
            if isinstance(episode, int) and isinstance(season, int):
                return season, episode
        except Exception as exc:  # Third-party parsing must not stop a batch.
            LOG.debug("guessit failed for %s: %s", path.name, exc)

    episode = safe_numeric_fallback(stem)
    return (1, episode) if episode is not None else None


def unique_conflict_path(folder: Path, original_name: str) -> Path:
    candidate = folder / original_name
    if not candidate.exists():
        return candidate
    stem, suffix = Path(original_name).stem, Path(original_name).suffix
    counter = 1
    while True:
        candidate = folder / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
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
) -> bool:
    action = "WOULD MOVE" if dry_run else "MOVE"
    LOG.info("%s: %s -> %s [%s]", action, source, destination, status)
    if dry_run:
        return True

    try:
        # Recheck at the last possible moment. This also protects callers if a
        # destination appeared after planning but before the move.
        if destination.exists():
            LOG.error("Refusing to overwrite existing destination: %s", destination)
            return False
        size = source.stat().st_size
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
    except OSError as exc:
        LOG.error("Failed moving %s: %s", source, exc)
        return False

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
    )
    if not append_history(history_folder / HISTORY_NAME, record):
        LOG.error("Move succeeded but history recording failed for %s", destination)
        # Best effort immediate rollback keeps an untracked move from lingering.
        try:
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(source))
            LOG.warning("Reverted unrecorded move: %s", source)
        except OSError as exc:
            LOG.critical("Could not revert unrecorded move %s: %s", destination, exc)
        return False
    return True


def organize_video(
    video: Path,
    series_name: str,
    library: Path,
    subtitles: list[Path],
    batch_id: str,
    dry_run: bool,
    operation: str = "organize",
) -> None:
    series_folder = library / series_name
    detected = detect_episode(video)

    if detected is None:
        target_folder = series_folder / "_Unsorted"
        target = target_folder / video.name
        if target.exists():
            target_folder = series_folder / "_Conflicts"
            target = unique_conflict_path(target_folder, video.name)
            status = "conflict"
        else:
            status = "unsorted"
        history_folder = series_folder
        moved = move_and_record(
            video, target, history_folder, "video", status, batch_id, dry_run,
            operation
        )
        if moved:
            for subtitle in subtitles:
                subtitle_target = target.with_suffix(subtitle.suffix)
                if subtitle_target.exists():
                    subtitle_target = unique_conflict_path(
                        series_folder / "_Conflicts", subtitle.name
                    )
                    subtitle_status = "conflict"
                else:
                    subtitle_status = status
                move_and_record(
                    subtitle, subtitle_target, history_folder, "subtitle",
                    subtitle_status, batch_id, dry_run, operation
                )
        return

    season, episode = detected
    season_folder = series_folder / f"Season {season:02d}"
    clean_stem = f"{series_file_title(series_name)} - S{season:02d}E{episode:02d}"
    target = season_folder / f"{clean_stem}{video.suffix}"
    if target.resolve(strict=False) == video.resolve(strict=False):
        LOG.info("SKIP (already correctly named): %s", video)
        return
    if target.exists():
        conflict_folder = series_folder / "_Conflicts"
        conflict_target = unique_conflict_path(conflict_folder, video.name)
        moved = move_and_record(
            video, conflict_target, series_folder, "video", "conflict",
            batch_id, dry_run, operation
        )
        if moved:
            for subtitle in subtitles:
                sub_target = unique_conflict_path(conflict_folder, subtitle.name)
                move_and_record(
                    subtitle, sub_target, series_folder, "subtitle", "conflict",
                    batch_id, dry_run, operation
                )
        return

    moved = move_and_record(
        video, target, season_folder, "video", "done", batch_id, dry_run,
        operation
    )
    if moved:
        for subtitle in subtitles:
            subtitle_target = season_folder / f"{clean_stem}{subtitle.suffix}"
            if subtitle_target.exists():
                subtitle_target = unique_conflict_path(
                    series_folder / "_Conflicts", subtitle.name
                )
                move_and_record(
                    subtitle, subtitle_target, series_folder, "subtitle", "conflict",
                    batch_id, dry_run, operation
                )
            else:
                move_and_record(
                    subtitle, subtitle_target, season_folder, "subtitle", "done",
                    batch_id, dry_run, operation
                )


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
    for season_folder in sorted(series_folder.iterdir(), key=lambda p: p.name.casefold()):
        if not season_folder.is_dir() or not re.fullmatch(
            r"(?i)Season \d{1,3}", season_folder.name
        ):
            continue
        videos = sorted(
            (
                path for path in season_folder.iterdir()
                if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
            ),
            key=lambda path: path.name.casefold(),
        )
        subtitles: dict[str, list[Path]] = {}
        for path in season_folder.iterdir():
            if path.is_file() and path.suffix.lower() in SUBTITLE_EXTENSIONS:
                subtitles.setdefault(path.stem.casefold(), []).append(path)
        for video in videos:
            videos_found += 1
            organize_video(
                video,
                series_folder.name,
                series_folder.parent,
                subtitles.get(video.stem.casefold(), []),
                batch_id,
                dry_run,
                operation="resort-existing",
            )
    if not videos_found:
        LOG.warning("No organized episode files found in Season folders.")
        return 0
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
    return 0


def run_organizer(
    series_folder: Path, library: Path | None = None, dry_run: bool = False
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

    for video in videos:
        matching_subtitles = subtitles_by_stem.pop(video.stem.casefold(), [])
        organize_video(
            video, series_folder.name, library, matching_subtitles,
            batch_id, dry_run
        )
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
    changed: set[Path] = set()
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
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(current), str(original))
            histories[history_path][index]["previous_status"] = record.get(
                "status", "done"
            )
            histories[history_path][index]["status"] = "undone"
            histories[history_path][index]["undone_timestamp"] = now_iso()
            changed.add(history_path)
            restored += 1
            LOG.info("RESTORED: %s -> %s", current, original)
        except OSError as exc:
            LOG.error("Failed restoring %s: %s", current, exc)
            skipped += 1

    for history_path in changed:
        save_history(history_path, histories[history_path])
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
    changed: set[Path] = set()
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
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(original), str(target))
            histories[history_path][index]["status"] = record.get(
                "previous_status", "done"
            )
            histories[history_path][index]["redone_timestamp"] = now_iso()
            changed.add(history_path)
            restored += 1
            LOG.info("REAPPLIED: %s -> %s", original, target)
        except OSError as exc:
            LOG.error("Failed reapplying %s: %s", original, exc)
            skipped += 1
    for history_path in changed:
        save_history(history_path, histories[history_path])
    return restored, skipped


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
        undone = [item for item in revisions if item["status"] == "undone"]
        selected = min(undone, key=lambda item: item["revision"], default=None)
    if not selected:
        LOG.warning("No revision is available to move %s.", direction)
        return 1

    files = list(history_files(series_folder))
    batch_id = selected["batch_id"]
    if direction == "back":
        moved, skipped = undo_records(files, batch_id)
    else:
        moved, skipped = redo_records(files, batch_id)
    sync_sort_revisions(series_folder)
    LOG.info(
        "Sort %s revision #%s: %d moved, %d skipped",
        direction, selected["revision"], moved, skipped,
    )
    return 0 if moved or not skipped else 1


def undo_batch(library: Path, batch_id: str) -> int:
    if not library.is_dir():
        LOG.error("Library does not exist: %s", library)
        return 2
    restored, skipped = undo_records(list(history_files(library)), batch_id)
    LOG.info("Undo batch %s: %d restored, %d skipped", batch_id, restored, skipped)
    return 0 if restored or not skipped else 1


def undo_last(library: Path) -> int:
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
    restored, skipped = undo_records(files, latest)
    LOG.info("Undo complete: %d restored, %d skipped", restored, skipped)
    return 0 if restored or not skipped else 1


def undo_folder(folder: Path) -> int:
    history_path = folder / HISTORY_NAME
    if not history_path.is_file():
        LOG.error("No %s found in %s", HISTORY_NAME, folder)
        return 2
    restored, skipped = undo_records([history_path])
    LOG.info("Folder undo: %d restored, %d skipped", restored, skipped)
    return 0 if restored or not skipped else 1


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
            return run_organizer(
                args.series_folder.expanduser(),
                args.library.expanduser() if args.library else None,
                dry_run=args.command == "dry-run",
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
