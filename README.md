# Jellyfin Video Manager

This main folder contains two independent projects:

```text
video-manager/
|-- organizer/                 # Filename and season organizer
|-- fuzzy_search/              # Optional fuzzy IMDb naming tool
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

## Updating on another PC

Clone the repository once instead of downloading a ZIP:

```powershell
git clone https://github.com/shiny222/vedio_name_manager__bot-telegram.git
```

Run each project's `install.bat` only for the first setup. For later updates,
close the bot and Local Bot API windows and double-click the root `update.bat`.
It runs a safe fast-forward `git pull`, checks dependencies, and preserves
ignored local files such as `config.json`, SQLite state, logs, and `.venv`.
