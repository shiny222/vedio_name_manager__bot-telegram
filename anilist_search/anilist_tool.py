#!/usr/bin/env python3
"""Independent AniList anime lookup and Jellyfin folder-name formatter."""

from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from rapidfuzz.fuzz import WRatio
except ImportError:
    WRatio = None


ROOT = Path(__file__).resolve().parent
CACHE_PATH = ROOT / "data" / "search_cache.json"
API_URL = "https://graphql.anilist.co"
USER_AGENT = "JellyfinVideoManager/1.0 AniListSearch"
INVALID_WINDOWS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
SERIES_FORMATS = {"TV", "TV_SHORT", "ONA", "OVA", "SPECIAL"}

SEARCH_QUERY = """
query ($search: String!, $perPage: Int!) {
  Page(page: 1, perPage: $perPage) {
    media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
      id
      title { romaji english native userPreferred }
      synonyms
      format
      seasonYear
      startDate { year }
      episodes
      status
    }
  }
}
"""


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", value.casefold())).strip()


def fuzzy_score(query: str, title: str) -> float:
    if WRatio is not None:
        return float(WRatio(query, title))
    return SequenceMatcher(None, normalized(query), normalized(title)).ratio() * 100


def sanitize_title(value: str) -> str:
    clean = INVALID_WINDOWS.sub("_", value).strip().rstrip(". ")
    return re.sub(r"\s+", " ", clean) or "Unknown Title"


def jellyfin_folder(title: str, year: int | None, anilist_id: int) -> str:
    """Use AniList's exact title/year; Jellyfin has no custom AniList ID tag."""
    name = sanitize_title(title)
    year_text = f" ({year})" if isinstance(year, int) and year > 1800 else ""
    if int(anilist_id) <= 0:
        raise ValueError("Invalid AniList media ID.")
    return f"{name}{year_text}"


def load_cache() -> dict:
    try:
        value = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CACHE_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(CACHE_PATH)


def fetch_results(query: str, limit: int, timeout: int) -> list[dict]:
    body = json.dumps(
        {
            "query": SEARCH_QUERY,
            "variables": {
                "search": query,
                "perPage": max(1, min(limit * 2, 20)),
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("AniList returned an invalid response.")
    errors = payload.get("errors")
    if errors:
        message = str(errors[0].get("message") if isinstance(errors[0], dict) else errors[0])
        raise RuntimeError(f"AniList returned an error: {message}")
    page = (payload.get("data") or {}).get("Page") or {}
    media = page.get("media") or []
    return media if isinstance(media, list) else []


def _matches_media_type(media_format: str, wanted: str) -> bool:
    value = media_format.upper()
    if wanted == "movie":
        return value == "MOVIE"
    if wanted == "series":
        return value in SERIES_FORMATS
    return value == "MOVIE" or value in SERIES_FORMATS


def _titles(item: dict) -> list[str]:
    title = item.get("title") if isinstance(item.get("title"), dict) else {}
    values = [
        title.get("english"),
        title.get("romaji"),
        title.get("userPreferred"),
        title.get("native"),
        *(item.get("synonyms") or []),
    ]
    output: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text.casefold() not in {saved.casefold() for saved in output}:
            output.append(text)
    return output


def parse_results(
    query: str, raw: list[dict], limit: int, media_type: str = "any"
) -> list[dict]:
    results: list[dict] = []
    for index, item in enumerate(raw):
        try:
            anilist_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        media_format = str(item.get("format") or "UNKNOWN").upper()
        if not _matches_media_type(media_format, media_type):
            continue
        titles = _titles(item)
        if not titles:
            continue
        title = titles[0]
        best_score = max(fuzzy_score(query, candidate) for candidate in titles)
        year = item.get("seasonYear") or (item.get("startDate") or {}).get("year")
        try:
            year = int(year) if year is not None else None
        except (TypeError, ValueError):
            year = None
        results.append(
            {
                "provider": "anilist",
                "provider_id": str(anilist_id),
                "anilist_id": anilist_id,
                "imdb_id": "",
                "title": title,
                "alternative_titles": titles[1:],
                "year": year,
                "type": media_format,
                "episodes": item.get("episodes"),
                "status": item.get("status"),
                "score": round(best_score, 1),
                "folder_name": jellyfin_folder(title, year, anilist_id),
                "_combined": best_score - min(index, 20) * 0.15,
            }
        )
    results.sort(key=lambda row: row["_combined"], reverse=True)
    for row in results:
        row.pop("_combined", None)
    return results[:limit]


def search(
    query: str,
    limit: int = 8,
    timeout: int = 12,
    media_type: str = "any",
) -> tuple[list[dict], str]:
    query = query.strip()
    if len(query) < 2:
        raise ValueError("Search query must contain at least two characters.")
    if media_type not in {"any", "movie", "series"}:
        raise ValueError("media_type must be any, movie, or series.")
    key = f"{media_type}:{normalized(query)}"
    cache = load_cache()
    try:
        results = parse_results(
            query, fetch_results(query, limit, timeout), limit, media_type
        )
        if results:
            cache[key] = {"saved_at": int(time.time()), "results": results}
            save_cache(cache)
        return results, "online"
    except (
        OSError,
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        RuntimeError,
        ValueError,
    ) as exc:
        cached = cache.get(key, {}).get("results")
        if isinstance(cached, list) and cached:
            return cached[:limit], "cache"
        raise RuntimeError(
            f"AniList search is unavailable and no cache exists: {exc}"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AniList search for anime Jellyfin folders"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    search_parser = sub.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=8)
    search_parser.add_argument("--timeout", type=int, default=12)
    search_parser.add_argument(
        "--media-type", choices=("any", "movie", "series"), default="any"
    )
    search_parser.add_argument("--json", action="store_true")
    format_parser = sub.add_parser("format")
    format_parser.add_argument("--title", required=True)
    format_parser.add_argument("--year", type=int)
    format_parser.add_argument("--anilist-id", required=True, type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "format":
            if args.anilist_id <= 0:
                raise ValueError("Invalid AniList media ID.")
            print(jellyfin_folder(args.title, args.year, args.anilist_id))
            return 0
        results, source = search(
            args.query,
            max(1, min(args.limit, 20)),
            max(2, args.timeout),
            args.media_type,
        )
        if args.json:
            print(
                json.dumps(
                    {"ok": True, "source": source, "results": results},
                    ensure_ascii=False,
                )
            )
        else:
            print(f"Source: {source}")
            for index, result in enumerate(results, 1):
                print(
                    f"{index}. {result['folder_name']} "
                    f"({result['type']}, match {result['score']}%)"
                )
        return 0 if results else 1
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc), "results": []}))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
