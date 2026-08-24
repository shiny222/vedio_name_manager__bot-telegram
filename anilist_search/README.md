# AniList Search Tool

An optional, standalone AniList title search for dedicated anime libraries.
It uses AniList's public GraphQL API and does not require an AniList account or
API key.

Install on Windows with `install.bat`, then search anime series:

```powershell
.\.venv\Scripts\python.exe anilist_tool.py search "BLEACH Sennen Kessen-hen" --media-type series
```

Search anime movies:

```powershell
.\.venv\Scripts\python.exe anilist_tool.py search "The Calamity" --media-type movie
```

JSON output for integrations:

```powershell
.\.venv\Scripts\python.exe anilist_tool.py search "Witch Hat Atelier" --media-type series --json
```

The Jellyfin folder format is:

```text
Official Anime Title (Year)
```

The AniList ID remains in the bot's SQLite queue identity. It is deliberately
not written as `[anilistid-...]`: Jellyfin documents folder-ID parsing for
built-in providers such as IMDb/TMDb/TVDb, while the AniList plugin does not
document a custom folder tag. The exact AniList title/year lets the enabled
AniList metadata provider perform its own lookup without displaying an unknown
tag as part of the title.

Successful exact queries are cached under `data`. If AniList is temporarily
unavailable, the tool can reuse that cached query. The Telegram bot keeps a
manual fallback, so this optional network tool cannot block downloads or the
organizers permanently.
