# IMDb Fuzzy Search Tool

An optional, standalone fuzzy title search for creating accurate Jellyfin folder
names. The Telegram bot does not require this project to queue, download, sort,
undo, scan Jellyfin, or use manual folder commands.

Install with `install.bat`, then search:

```powershell
.\.venv\Scripts\python.exe imdb_tool.py search "dr ston"
```

JSON output for integrations:

```powershell
.\.venv\Scripts\python.exe imdb_tool.py search "dr ston" --json
```

Example result:

```text
Dr. Stone (2019) [imdbid-tt9679542]
```

Successful results are cached under `data`. If IMDb is temporarily unavailable,
an exact previous query can use its cached results. This tool uses IMDb's public
search-suggestion service, which may change or become unavailable; manual folder
naming remains the fallback.
