# Telegram Jellyfin Bot

This bot watches an allowed Telegram group or channel, queues video files, downloads them only after your command/confirmation, and can optionally call the separate Jellyfin organizer and IMDb fuzzy-title tool.

The bot is intentionally separate from the other tools:

- `telegram_jellyfin_bot` downloads and manages the queue.
- `organizer` sorts/renames files for Jellyfin.
- `fuzzy_search` is optional and only helps suggest official folder names.

## Requirements

- Windows
- Python 3.10 or newer
- A Telegram bot token from [BotFather](https://t.me/BotFather)
- `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org)
- `telegram-bot-api.exe` for Windows if you want large local downloads

## Quick install

1. Open the `telegram_jellyfin_bot` folder.
2. Run `install.bat`.
3. Put `telegram-bot-api.exe` and its DLL files in the `tools` folder, or set the real path in `config.json`.
4. Edit `config.json`.
5. Start `run_local_bot_api.bat`.
6. Start `run.bat`.

Do not commit or share `config.json`; it contains your token and private settings.

## Important config fields

- `bot_token`: your BotFather token.
- `telegram_api_id` and `telegram_api_hash`: values from my.telegram.org.
- `jellyfin_library_path`: your main Jellyfin shows/anime library folder.
- `allowed_chat_ids`: the Telegram group/channel IDs that may use the bot.
- `confirm_before_download`: if `true`, the bot waits for `/confirm_download`.
- `ask_before_overwrite`: if `true`, the bot asks before handling duplicate files.
- `sorter_command`: command used to call the independent `organizer` tool.
- `jellyfin_server_url` and `jellyfin_api_key`: needed for `/jellyfin_scan`.
- `fuzzy_search_command`: command used to call the optional IMDb fuzzy search tool.

If `allowed_chat_ids` is empty, every chat that can reach the bot is allowed. Use `/chatid` to find your chat ID, then add it to config.

## Normal usage

Set or choose a destination folder:

```text
/setfolder My Anime
/folders
/usefolder Existing Anime Folder
```

Send videos to the bot in the allowed group or channel. The bot adds them to the queue but does not download immediately.

Review and start downloads:

```text
/download
/confirm_download
```

Sort files after download:

```text
/sort_current
/sort_latest
/resort_current
```

Trigger Jellyfin scan:

```text
/jellyfin_scan
```

## Commands

- `/menu` — show the button menu.
- `/setfolder NAME` — set a destination folder, using optional IMDb fuzzy search first.
- `/folders` — choose from existing Jellyfin folders.
- `/usefolder NAME` — use an existing folder by exact name.
- `/renamefolder NAME` — safely rename the current folder.
- `/folder` — show the current folder.
- `/unsetfolder` — clear the current folder.
- `/queue` — show queued files.
- `/remove ID` — remove one queued file.
- `/clearqueue` — clear active queue items.
- `/download` — show download summary.
- `/confirm_download` — start download after confirmation.
- `/status` — show queue counts, `.part` files, and tracked background tasks.
- `/cancel` — request cancellation.
- `/resolve ID skip|overwrite|save_with_suffix` — handle a duplicate destination file.
- `/sort_current` — sort only new loose files in the current folder.
- `/resort_current` — rename already sorted files to match the current folder name.
- `/sort_history` — show numbered sorter revisions.
- `/sort_back` — move one sorter revision back.
- `/sort_forward` — move one sorter revision forward.
- `/sort_latest` — sort the latest downloaded folder.
- `/sort_folder NAME` — sort a specific folder in the library.
- `/sort_status` — show latest sorter run status.
- `/undo_sort_last` — undo the latest sorter batch.
- `/undo_sort_batch ID` — undo a specific sorter batch.
- `/jellyfin_scan` — request a full Jellyfin library scan.
- `/jellyfin_status` — test Jellyfin connection and show scan status.
- `/episodes [NAME]` — show known and missing episodes for one series.
- `/library_episodes` — show an episode summary for the whole library.
- `/imdb_search NAME` — fuzzy-search an official IMDb folder name.
- `/imdb_fix_current [NAME]` — safely rename the current folder using IMDb results.
- `/chatid` — show the current chat ID.
- `/help` — show command help and copy buttons.

## Channel command buttons

Telegram channels cannot pre-fill editable slash commands the same way normal chats can. For commands that need extra text, `/help` shows copy buttons. Tap a button, paste the command, then add the folder name or batch ID.

## Duplicate files

The bot does not overwrite automatically. If a destination file already exists, it asks you to choose:

```text
/resolve 12 skip
/resolve 12 overwrite
/resolve 12 save_with_suffix
```

`save_with_suffix` creates a name like `Video (1).mkv`.

## Background task tracking

The bot now tracks background tasks instead of launching silent fire-and-forget jobs. Downloads, sorter runs, IMDb searches, undo actions, and Jellyfin scans are stored in an in-memory task set until they finish.

When a task fails, the error is logged. `/status` also shows the current number of tracked background tasks.

## Tests

From the project root:

```bat
telegram_jellyfin_bot\.venv\Scripts\python.exe -m unittest telegram_jellyfin_bot.tests.test_core -v
```

The real `getMe` integration test runs only when local API integration is enabled in the test environment.

## Troubleshooting

- `telegram-bot-api.exe was not found`: fix the path in `config.json`.
- `Connection refused`: keep `run_local_bot_api.bat` open before running the bot.
- `Unauthorized`: the token is wrong, or the bot still needs to be logged out from the public API before using Local Bot API.
- The bot does not see channel posts: add it as a channel administrator and check `allowed_chat_ids`.
- Files do not enter the queue: check the extension and `allowed_video_extensions`.
- Sorter does not run: check Python and `organizer.py` paths inside `sorter_command`.
- `.part` file remains: the download was interrupted; run `/download` again.
