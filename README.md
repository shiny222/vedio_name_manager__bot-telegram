# Jellyfin Video Manager

This main folder contains two independent projects:

```text
video-manager/
|-- organizer/                 # Filename and season organizer
`-- telegram_jellyfin_bot/     # Telegram queue and download bot
```

Open `organizer` for the naming tool. Run its `install.bat` once, then use
`start_organizer.bat`.

Open `telegram_jellyfin_bot` for the Telegram bot. Run its `install.bat`,
configure `config.json`, then start `run_local_bot_api.bat` and `run.bat`.

The bot works without the organizer; only its `/sort_*` commands require the
sibling `organizer` folder.
