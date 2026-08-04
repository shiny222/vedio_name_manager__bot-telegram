#!/usr/bin/env python3
"""Safely import one confirmed movie into a Jellyfin movie library."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".webm", ".m4v"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".vtt"}
HISTORY_NAME = ".rename_history.json"
JOURNAL_NAME = ".movie_operation_journal.jsonl"
INVALID_WINDOWS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
IMDB_ID = re.compile(r"tt\d{5,12}", re.IGNORECASE)
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
LOG = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_title(value: str) -> str:
    title = re.sub(r"\s+", " ", INVALID_WINDOWS.sub("_", value)).strip().rstrip(". ")
    if not title or title in {".", ".."} or title.upper() in WINDOWS_RESERVED:
        raise ValueError("Movie title is empty or unsafe.")
    return title


def validate_year(value: int | None) -> int | None:
    if value is None:
        return None
    if not 1878 <= value <= 9999:
        raise ValueError("Movie year must be a four-digit year from 1878 onward.")
    return value


def validate_imdb_id(value: str) -> str:
    value = value.strip().lower()
    if value and not IMDB_ID.fullmatch(value):
        raise ValueError("IMDb ID must look like tt1234567.")
    return value


def movie_base_name(title: str, year: int | None, imdb_id: str) -> str:
    base = sanitize_title(title)
    if year is not None:
        base += f" ({validate_year(year)})"
    imdb_id = validate_imdb_id(imdb_id)
    if imdb_id:
        base += f" [imdbid-{imdb_id}]"
    return base


def safe_child(root: Path, name: str) -> Path:
    root = root.resolve()
    candidate = (root / name).resolve()
    if candidate.parent != root:
        raise ValueError("Movie destination escaped the configured library.")
    return candidate


def load_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid history file {path}: {exc}") from exc
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError(f"History file is not a JSON record list: {path}")
    return data


def save_history(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(records, output, ensure_ascii=False, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    temp.replace(path)


def append_journal(folder: Path, phase: str, details: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": now_iso(), "phase": phase, **details}
    with (folder / JOURNAL_NAME).open("a", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps(payload, ensure_ascii=False) + "\n")
        output.flush()
        os.fsync(output.fileno())


@dataclass(frozen=True)
class MovePlan:
    source: Path
    destination: Path
    file_type: str
    size: int


def _subtitle_suffix(video: Path, subtitle: Path) -> str | None:
    video_stem = video.stem
    subtitle_stem = subtitle.stem
    if subtitle_stem.casefold() == video_stem.casefold():
        return ""
    prefix = video_stem + "."
    if subtitle_stem.casefold().startswith(prefix.casefold()):
        return subtitle_stem[len(video_stem):]
    return None


def matching_subtitles(video: Path) -> list[tuple[Path, str]]:
    matches: list[tuple[Path, str]] = []
    for item in video.parent.iterdir():
        if not item.is_file() or item.suffix.lower() not in SUBTITLE_EXTENSIONS:
            continue
        suffix = _subtitle_suffix(video, item)
        if suffix is not None:
            matches.append((item, suffix))
    return sorted(matches, key=lambda pair: pair[0].name.casefold())


def plan_import(
    source: Path,
    library: Path,
    title: str,
    year: int | None,
    imdb_id: str,
) -> tuple[str, Path, list[MovePlan]]:
    source = source.expanduser().resolve()
    library = library.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Movie source file does not exist: {source}")
    if source.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError(f"Unsupported movie extension: {source.suffix}")
    if source == library or library in source.parents:
        raise ValueError("Movie source must be outside the final movie library.")

    base_name = movie_base_name(title, year, imdb_id)
    destination_folder = safe_child(library, base_name)
    destination_video = destination_folder / f"{base_name}{source.suffix}"

    if destination_folder.exists():
        existing_videos = [
            item for item in destination_folder.iterdir()
            if item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS
        ]
        if existing_videos:
            names = ", ".join(item.name for item in existing_videos[:3])
            raise FileExistsError(
                "Movie folder already contains a video; nothing was overwritten: " + names
            )

    plans = [MovePlan(source, destination_video, "video", source.stat().st_size)]
    for subtitle, language_suffix in matching_subtitles(source):
        target = destination_folder / (
            f"{base_name}{language_suffix}{subtitle.suffix}"
        )
        plans.append(MovePlan(subtitle, target, "subtitle", subtitle.stat().st_size))

    duplicates = {plan.destination for plan in plans if plan.destination.exists()}
    if duplicates:
        raise FileExistsError(
            "Destination already exists; nothing was overwritten: "
            + ", ".join(sorted(path.name for path in duplicates))
        )
    return base_name, destination_folder, plans


def _verified_move(source: Path, destination: Path, expected_size: int) -> bool:
    try:
        return (
            not source.exists()
            and destination.is_file()
            and destination.stat().st_size == expected_size
        )
    except OSError:
        return False


def _rollback_move(destination: Path, source: Path, expected_size: int) -> bool:
    try:
        if source.exists() or not destination.is_file():
            return False
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(destination), str(source))
        return _verified_move(destination, source, expected_size)
    except OSError:
        return False


def move_and_record(plan: MovePlan, history_path: Path, batch_id: str) -> dict:
    operation_id = uuid.uuid4().hex
    details = {
        "operation_id": operation_id,
        "batch_id": batch_id,
        "original_full_path": str(plan.source),
        "new_full_path": str(plan.destination),
        "file_size": plan.size,
        "file_type": plan.file_type,
    }
    append_journal(history_path.parent, "move-planned", details)
    plan.destination.parent.mkdir(parents=True, exist_ok=True)
    if plan.destination.exists():
        append_journal(history_path.parent, "move-conflict", details)
        raise FileExistsError(f"Destination appeared during import: {plan.destination}")
    shutil.move(str(plan.source), str(plan.destination))
    if not _verified_move(plan.source, plan.destination, plan.size):
        rolled_back = _rollback_move(plan.destination, plan.source, plan.size)
        append_journal(
            history_path.parent,
            "move-rolled-back" if rolled_back else "move-verification-failed",
            details,
        )
        raise OSError(f"Move verification failed: {plan.source} -> {plan.destination}")

    record = {
        "timestamp": now_iso(),
        "original_full_path": str(plan.source),
        "new_full_path": str(plan.destination),
        "original_filename": plan.source.name,
        "new_filename": plan.destination.name,
        "file_size": plan.size,
        "file_type": plan.file_type,
        "status": "done",
        "batch_id": batch_id,
        "operation_id": operation_id,
    }
    records = load_history(history_path)
    records.append(record)
    try:
        save_history(history_path, records)
    except Exception:
        rolled_back = _rollback_move(plan.destination, plan.source, plan.size)
        append_journal(
            history_path.parent,
            "history-save-rolled-back" if rolled_back else "history-save-rollback-failed",
            details,
        )
        raise
    append_journal(history_path.parent, "move-done", details)
    return record


def history_files(root: Path) -> Iterable[Path]:
    if root.is_file() and root.name == HISTORY_NAME:
        yield root
    elif root.is_dir():
        yield from root.rglob(HISTORY_NAME)


def _active_records(
    files: Iterable[Path], batch_id: str | None = None
) -> list[tuple[str, Path, int, dict]]:
    candidates: list[tuple[str, Path, int, dict]] = []
    for history_path in files:
        for index, record in enumerate(load_history(history_path)):
            if record.get("status") == "undone":
                continue
            if batch_id is not None and record.get("batch_id") != batch_id:
                continue
            candidates.append((str(record.get("timestamp", "")), history_path, index, record))
    return candidates


def undo_records(candidates: list[tuple[str, Path, int, dict]]) -> tuple[int, int]:
    histories: dict[Path, list[dict]] = {}
    restored = skipped = 0
    for _, history_path, index, record in sorted(
        candidates, key=lambda entry: entry[0], reverse=True
    ):
        if history_path not in histories:
            histories[history_path] = load_history(history_path)
        records = histories[history_path]
        current = Path(record["new_full_path"])
        original = Path(record["original_full_path"])
        expected_size = record.get("file_size")
        try:
            if original.exists() or not current.is_file():
                skipped += 1
                continue
            actual_size = current.stat().st_size
            if isinstance(expected_size, int) and actual_size != expected_size:
                skipped += 1
                continue
            details = {
                "operation_id": uuid.uuid4().hex,
                "related_operation_id": record.get("operation_id", ""),
                "batch_id": record.get("batch_id", ""),
                "original_full_path": str(original),
                "new_full_path": str(current),
                "file_size": actual_size,
            }
            append_journal(history_path.parent, "undo-planned", details)
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(current), str(original))
            if not _verified_move(current, original, actual_size):
                _rollback_move(original, current, actual_size)
                append_journal(history_path.parent, "undo-verification-failed", details)
                skipped += 1
                continue
            previous = dict(records[index])
            records[index]["status"] = "undone"
            records[index]["undone_timestamp"] = now_iso()
            try:
                save_history(history_path, records)
            except Exception:
                records[index] = previous
                _rollback_move(original, current, actual_size)
                append_journal(history_path.parent, "undo-history-save-failed", details)
                skipped += 1
                continue
            append_journal(history_path.parent, "undo-done", details)
            restored += 1
        except OSError as exc:
            LOG.error("Undo failed for %s: %s", current, exc)
            skipped += 1
    return restored, skipped


def import_movie(
    source: Path,
    library: Path,
    title: str,
    year: int | None,
    imdb_id: str,
    dry_run: bool = False,
) -> dict:
    base_name, destination_folder, plans = plan_import(
        source, library, title, year, imdb_id
    )
    batch_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    result = {
        "ok": True,
        "dry_run": dry_run,
        "batch_id": batch_id,
        "movie_name": base_name,
        "destination": str(destination_folder),
        "files": [
            {
                "source": str(plan.source),
                "destination": str(plan.destination),
                "file_type": plan.file_type,
                "file_size": plan.size,
            }
            for plan in plans
        ],
    }
    if dry_run:
        return result

    history_path = destination_folder / HISTORY_NAME
    completed: list[dict] = []
    try:
        for plan in plans:
            completed.append(move_and_record(plan, history_path, batch_id))
    except Exception:
        if completed:
            undo_records(_active_records([history_path], batch_id))
        raise
    return result


def undo_batch(library: Path, batch_id: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", batch_id):
        raise ValueError("Invalid batch ID.")
    files = list(history_files(library.expanduser().resolve()))
    candidates = _active_records(files, batch_id)
    if not candidates:
        raise ValueError(f"No active movie import batch was found: {batch_id}")
    restored, skipped = undo_records(candidates)
    return {
        "ok": skipped == 0 and restored == len(candidates),
        "batch_id": batch_id,
        "restored": restored,
        "skipped": skipped,
    }


def undo_last(library: Path) -> dict:
    files = list(history_files(library.expanduser().resolve()))
    candidates = _active_records(files)
    batches: dict[str, str] = {}
    for timestamp, _, _, record in candidates:
        batch_id = str(record.get("batch_id", ""))
        if batch_id:
            batches[batch_id] = max(timestamp, batches.get(batch_id, ""))
    if not batches:
        raise ValueError("No active movie import batch was found.")
    return undo_batch(library, max(batches, key=batches.get))


def undo_folder(folder: Path) -> dict:
    folder = folder.expanduser().resolve()
    history_path = folder / HISTORY_NAME
    candidates = _active_records([history_path]) if history_path.is_file() else []
    if not candidates:
        raise ValueError(f"No active movie history was found in: {folder}")
    restored, skipped = undo_records(candidates)
    return {
        "ok": skipped == 0 and restored == len(candidates),
        "folder": str(folder),
        "restored": restored,
        "skipped": skipped,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import one confirmed movie into a Jellyfin movie library."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("import", "dry-run"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--source", required=True, type=Path)
        sub.add_argument("--library", required=True, type=Path)
        sub.add_argument("--title", required=True)
        sub.add_argument("--year", type=int)
        sub.add_argument("--imdb-id", default="")
        sub.add_argument("--json", action="store_true")
    last = subparsers.add_parser("undo-last")
    last.add_argument("--library", required=True, type=Path)
    last.add_argument("--json", action="store_true")
    batch = subparsers.add_parser("undo-batch")
    batch.add_argument("batch_id")
    batch.add_argument("--library", required=True, type=Path)
    batch.add_argument("--json", action="store_true")
    folder = subparsers.add_parser("undo-folder")
    folder.add_argument("folder_path", type=Path)
    folder.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        if args.command in {"import", "dry-run"}:
            result = import_movie(
                args.source,
                args.library,
                args.title,
                args.year,
                args.imdb_id,
                dry_run=args.command == "dry-run",
            )
        elif args.command == "undo-last":
            result = undo_last(args.library)
        elif args.command == "undo-batch":
            result = undo_batch(args.library, args.batch_id)
        else:
            result = undo_folder(args.folder_path)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    except Exception as exc:
        LOG.error("%s", exc)
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
