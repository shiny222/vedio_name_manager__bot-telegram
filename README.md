# Jellyfin Video Manager

This main folder contains four independent projects:

```text
video-manager/
|-- organizer/                 # Filename and season organizer
|-- fuzzy_search/              # Optional fuzzy IMDb naming tool
|-- movie_organizer/           # Safe one-movie Jellyfin importer
`-- telegram_jellyfin_bot/     # Telegram queue and download bot
```

Open `organizer` for the naming tool. Run its `install.bat` once, then use
`start_organizer.bat`.

Open `telegram_jellyfin_bot` for the Telegram bot. Run its `install.bat`,
configure `config.json`, then start `run_local_bot_api.bat` and `run.bat`.

The bot works without the organizer; only its `/sort_*` commands require the
sibling `organizer` folder.

The optional `fuzzy_search` project searches IMDb fuzzily and generates
Jellyfin-compatible folder names. Run its `install.bat` once to enable the
bot's `/imdb_search` and `/imdb_fix_current` commands. The main bot continues
working if this tool or IMDb is unavailable.

The optional `movie_organizer` project imports a confirmed movie from a staging
folder into a separate Jellyfin Movies library. Run its `install.bat` once to
enable the bot's movie mode. The movie organizer can also be used directly and
does not import bot code or contact IMDb.

## NAS Docker deployment

The repository now includes separate NAS Compose projects for Video Manager,
Local Telegram Bot API, and n8n. The two custom images are built locally from a
Git clone on the NAS, while runtime secrets and state remain outside Git.

See [`nas/README.md`](nas/README.md) for the exact OpenMediaVault paths, initial
build commands, shared Docker network, persistent mounts, and update procedure.
The NAS bot exposes all four mounted Jellyfin libraries simultaneously; use
`/libraries` to choose the destination independently in each Telegram chat.

## Updating on another PC

Clone the repository once instead of downloading a ZIP:

```powershell
git clone https://github.com/shiny222/vedio_name_manager__bot-telegram.git
```

Run each enabled project's `install.bat` only for the first setup. For later updates,
close the bot and Local Bot API windows and double-click the root `update.bat`.
It runs a safe fast-forward `git pull`, checks dependencies, and preserves
ignored local files such as `config.json`, SQLite state, logs, and `.venv`.
