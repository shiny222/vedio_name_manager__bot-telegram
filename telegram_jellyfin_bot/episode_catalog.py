from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

RESOLUTIONS = {360, 480, 720, 1080, 1440, 2160}


def normalize_digits(value: str) -> str:
    source = "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩０１２３４５６７８９"
    target = "012345678901234567890123456789"
    return value.translate(str.maketrans(source, target))


def detect_episode(filename: str) -> tuple[int, int] | None:
    """Conservatively detect one season/episode identity from a filename."""
    stem = normalize_digits(Path(filename).stem)
    paired = (
        r"(?i)(?<![a-z0-9])s(?P<s>\d{1,3})[ ._-]*e(?P<e>\d{1,4})(?!\d)",
        r"(?i)(?<!\d)(?P<s>\d{1,3})[ ._-]*x[ ._-]*(?P<e>\d{1,4})(?!\d)",
        r"(?i)\bseason[ ._-]*(?P<s>\d{1,3})[ ._-]*"
        r"(?:episode|ep|e)[ ._-]*(?P<e>\d{1,4})(?!\d)",
        r"(?i)(?<![a-z0-9])s(?P<s>\d{1,3})[ ._-]*"
        r"(?:episode|ep)[ ._-]*(?P<e>\d{1,4})(?!\d)",
        r"(?i)(?:episode|ep|e)[ ._-]*(?P<e>\d{1,4})[ ._-]+"
        r"s(?:eason)?[ ._-]*(?P<s>\d{1,3})(?!\d)",
        r"(?:فصل|الموسم)[\s._-]*(?P<s>\d{1,3})[\s._-]*"
        r"(?:قسمت|حلقة|الحلقة)[\s._-]*(?P<e>\d{1,4})",
    )
    for pattern in paired:
        match = re.search(pattern, stem)
        if match:
            return int(match["s"]), int(match["e"])

    for pattern in (
        r"(?i)(?<![a-z0-9])s(?P<s>\d{1,3})\s*[-._ ]+\s*"
        r"(?P<e>\d{1,4})(?!\d|p\b)",
        r"(?i)\bseason[ ._-]*(?P<s>\d{1,3})\s*[-._ ]+\s*"
        r"(?P<e>\d{1,4})(?!\d|p\b)",
    ):
        match = re.search(pattern, stem)
        if match:
            episode = int(match["e"])
            if episode not in RESOLUTIONS:
                return int(match["s"]), episode

    episode_only = (
        r"(?i)(?:episode|ep)[ ._-]*(?P<e>\d{1,4})(?:v\d+)?(?!\d)",
        r"(?i)(?<![a-z0-9])e[ ._-]*(?P<e>\d{1,4})(?!\d)",
        r"(?:قسمت|حلقة|الحلقة)[\s._-]*(?P<e>\d{1,4})",
        r"(?:第\s*)?(?P<e>\d{1,4})\s*話",
        r"(?P<e>\d{1,4})\s*화",
    )
    for pattern in episode_only:
        match = re.search(pattern, stem)
        if match:
            return 1, int(match["e"])

    # Anime absolute numbering such as "Title - 025 [1080p]".
    match = re.search(
        r"(?i)\s-\s*(?P<e>\d{1,4})(?:v\d+)?"
        r"(?:\s*(?:\[|\(|$))",
        stem,
    )
    if match:
        episode = int(match["e"])
        if episode not in RESOLUTIONS and not 1900 <= episode <= 2099:
            return 1, episode
    return None


@dataclass(frozen=True)
class EpisodeEntry:
    season: int
    episode: int
    path: Path


class EpisodeCatalog:
    def __init__(self, video_extensions: set[str]):
        self.video_extensions = {ext.lower() for ext in video_extensions}

    def scan_series(self, series_folder: Path) -> list[EpisodeEntry]:
        if not series_folder.is_dir():
            return []
        entries: list[EpisodeEntry] = []
        for path in series_folder.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in self.video_extensions:
                continue
            relative_parts = path.relative_to(series_folder).parts[:-1]
            if any(part in {"_Unsorted", "_Conflicts"} for part in relative_parts):
                continue
            detected = detect_episode(path.name)
            if detected:
                entries.append(EpisodeEntry(*detected, path))
        return entries

    @staticmethod
    def grouped(entries: list[EpisodeEntry]) -> dict[int, set[int]]:
        result: dict[int, set[int]] = {}
        for entry in entries:
            result.setdefault(entry.season, set()).add(entry.episode)
        return result

    def contains(self, series_folder: Path, season: int, episode: int) -> EpisodeEntry | None:
        return next(
            (
                item for item in self.scan_series(series_folder)
                if item.season == season and item.episode == episode
            ),
            None,
        )


def compact_numbers(numbers: set[int]) -> str:
    if not numbers:
        return "—"
    ordered = sorted(numbers)
    ranges: list[str] = []
    start = previous = ordered[0]
    for number in ordered[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append(
            f"{start:02d}-{previous:02d}" if start != previous else f"{start:02d}"
        )
        start = previous = number
    ranges.append(
        f"{start:02d}-{previous:02d}" if start != previous else f"{start:02d}"
    )
    return ", ".join(ranges)


def format_series_inventory(name: str, entries: list[EpisodeEntry]) -> str:
    grouped = EpisodeCatalog.grouped(entries)
    if not grouped:
        return f"{name}: هیچ اپیزود قابل‌شناسایی پیدا نشد."
    lines = [f"🎬 {name}", f"مجموع: {sum(len(x) for x in grouped.values())} اپیزود"]
    for season in sorted(grouped):
        episodes = grouped[season]
        missing = set(range(1, max(episodes) + 1)) - episodes
        lines.append(f"S{season:02d}: {compact_numbers(episodes)}")
        if missing:
            lines.append(f"  Missing: {compact_numbers(missing)}")
    return "\n".join(lines)
