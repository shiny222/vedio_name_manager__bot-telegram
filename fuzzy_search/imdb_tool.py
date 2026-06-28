#!/usr/bin/env python3
"""Independent fuzzy IMDb title lookup and Jellyfin folder-name formatter."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote

try:
    from rapidfuzz.fuzz import WRatio
except ImportError:
    WRatio = None

ROOT = Path(__file__).resolve().parent
CACHE_PATH = ROOT / "data" / "search_cache.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JellyfinFuzzySearchTool/1.0"
INVALID_WINDOWS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
ALLOWED_TYPES = {
    "feature", "movie", "TV movie", "TV series", "TV mini-series",
    "TV special", "video", "short", "TV short",
}


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", value.casefold())).strip()


def score(query: str, title: str) -> float:
    if WRatio is not None:
        return float(WRatio(query, title))
    return SequenceMatcher(None, normalized(query), normalized(title)).ratio() * 100


def sanitize_title(value: str) -> str:
    clean = INVALID_WINDOWS.sub("_", value).strip().rstrip(". ")
    return re.sub(r"\s+", " ", clean) or "Unknown Title"


def jellyfin_folder(title: str, year: int | None, imdb_id: str) -> str:
    name = sanitize_title(title)
    year_text = f" ({year})" if isinstance(year, int) and year > 1800 else ""
    return f"{name}{year_text} [imdbid-{imdb_id}]"


def load_cache() -> dict:
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = CACHE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(CACHE_PATH)


def fetch_suggestions(query: str, timeout: int) -> list[dict]:
    slug = quote(re.sub(r"\s+", "_", query.strip().casefold()), safe="_")
    first = next((char for char in slug if char.isalnum()), "x")
    url = f"https://v3.sg.media-imdb.com/suggestion/{first}/{slug}.json"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    return payload.get("d", []) if isinstance(payload, dict) else []


def parse_results(query: str, raw: list[dict], limit: int) -> list[dict]:
    results = []
    for index, item in enumerate(raw):
        imdb_id = str(item.get("id", ""))
        title = str(item.get("l", "")).strip()
        media_type = str(item.get("q") or item.get("qid") or "title")
        if not re.fullmatch(r"tt\d{5,12}", imdb_id) or not title:
            continue
        if media_type not in ALLOWED_TYPES and "series" not in media_type.lower():
            continue
        year = item.get("y")
        year = int(year) if isinstance(year, (int, float)) else None
        fuzzy = score(query, title)
        # IMDb already ranks suggestions; fuzzy score dominates while preserving
        # IMDb's order for similarly named titles.
        combined = fuzzy - min(index, 20) * 0.15
        results.append(
            {
                "imdb_id": imdb_id,
                "title": title,
                "year": year,
                "type": media_type,
                "score": round(fuzzy, 1),
                "folder_name": jellyfin_folder(title, year, imdb_id),
                "_combined": combined,
            }
        )
    results.sort(key=lambda row: row["_combined"], reverse=True)
    for row in results:
        row.pop("_combined", None)
    return results[:limit]


def search(query: str, limit: int = 8, timeout: int = 12) -> tuple[list[dict], str]:
    query = query.strip()
    if len(query) < 2:
        raise ValueError("Search query must contain at least two characters.")
    key = normalized(query)
    cache = load_cache()
    try:
        results = parse_results(query, fetch_suggestions(query, timeout), limit)
        if results:
            cache[key] = {"saved_at": int(time.time()), "results": results}
            save_cache(cache)
        return results, "online"
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        cached = cache.get(key, {}).get("results")
        if isinstance(cached, list) and cached:
            return cached[:limit], "cache"
        raise RuntimeError(f"IMDb search is unavailable and no cache exists: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fuzzy IMDb search for Jellyfin folders")
    sub = parser.add_subparsers(dest="command", required=True)
    search_parser = sub.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=8)
    search_parser.add_argument("--timeout", type=int, default=12)
    search_parser.add_argument("--json", action="store_true")
    format_parser = sub.add_parser("format")
    format_parser.add_argument("--title", required=True)
    format_parser.add_argument("--year", type=int)
    format_parser.add_argument("--imdb-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "format":
            if not re.fullmatch(r"tt\d{5,12}", args.imdb_id):
                raise ValueError("Invalid IMDb title ID.")
            print(jellyfin_folder(args.title, args.year, args.imdb_id))
            return 0
        results, source = search(
            args.query, max(1, min(args.limit, 20)), max(2, args.timeout)
        )
        if args.json:
            print(json.dumps({"ok": True, "source": source, "results": results}, ensure_ascii=False))
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
