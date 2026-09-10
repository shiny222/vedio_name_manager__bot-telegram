"""Local, conservative grouping of series filenames before remote resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ParsedSeriesFile:
    filename: str
    candidate_title: str
    year: int | None
    season: int | None
    episode: int | None
    extension: str


@dataclass
class SeriesFileGroup:
    key: str
    candidate_title: str
    year: int | None
    files: list[ParsedSeriesFile]


_EPISODE_RE = re.compile(
    r"(?ix)(?<![a-z0-9])(?:s\d{1,3}\s*[._ -]*e\d{1,4}|\d{1,3}\s*x\s*\d{1,4})"
    r"|\bseason\s*\d{1,3}\s*(?:episode|ep|e)\s*\d{1,4}"
)
_YEAR_RE = re.compile(r"(?<!\d)((?:18|19|20)\d{2})(?!\d)")
_NOISE_RE = re.compile(
    r"(?ix)\b(?:2160p|1440p|1080p|720p|576p|480p|4k|8k|web[- .]?dl|web[- .]?rip|"
    r"bluray|brrip|hdtv|dvdrip|remux|x26[45]|hevc|h264|aac|ac3|dts|proper|repack)\b.*$"
)


def normalize_group_title(value: str) -> str:
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip().casefold()


def parse_series_filename(filename: str, episode: tuple[int, int] | None = None) -> ParsedSeriesFile:
    path = Path(filename)
    stem = path.stem
    match = _EPISODE_RE.search(stem)
    season = episode[0] if episode else None
    number = episode[1] if episode else None
    if match and episode is None:
        marker = match.group(0).replace(" ", "")
        parts = re.search(r"(?i)s(\d+)e(\d+)|(?<!\d)(\d+)x(\d+)", marker)
        if parts:
            season = int(parts.group(1) or parts.group(3))
            number = int(parts.group(2) or parts.group(4))
    title_part = stem[: match.start()] if match else stem
    year_match = _YEAR_RE.search(title_part)
    year = int(year_match.group(1)) if year_match else None
    if year_match:
        title_part = title_part[:year_match.start()]
    title_part = _NOISE_RE.sub("", title_part)
    title_part = re.sub(r"[._]+", " ", title_part)
    title_part = re.sub(r"\s+", " ", title_part).strip(" -_")
    return ParsedSeriesFile(path.name, title_part, year, season, number, path.suffix.lower())


def group_series_filenames(filenames: list[str] | tuple[str, ...]) -> list[SeriesFileGroup]:
    """Group only equal normalized titles, keeping conflicting years separate."""
    groups: dict[tuple[str, int | None], SeriesFileGroup] = {}
    unresolved: list[ParsedSeriesFile] = []
    for filename in filenames:
        parsed = parse_series_filename(filename)
        title_key = normalize_group_title(parsed.candidate_title)
        if not title_key:
            unresolved.append(parsed)
            continue
        key = (title_key, parsed.year)
        group = groups.get(key)
        if group is None:
            group = SeriesFileGroup(f"{title_key}:{parsed.year or ''}", parsed.candidate_title, parsed.year, [])
            groups[key] = group
        group.files.append(parsed)
    # A title with no year must not silently absorb same-title files with a year.
    # Unparseable names remain independent so a bad filename cannot merge files.
    for parsed in unresolved:
        groups[(f"__unresolved__:{parsed.filename.casefold()}", None)] = SeriesFileGroup(
            f"unresolved:{parsed.filename.casefold()}", parsed.candidate_title, parsed.year, [parsed]
        )
    return list(groups.values())
