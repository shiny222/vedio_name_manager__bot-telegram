# Telegram Jellyfin Bot

This bot watches an allowed Telegram group or channel, queues video files,
downloads them only after your command/confirmation, and can optionally call
the separate series organizer, movie organizer, and IMDb fuzzy-title tool.

The bot is intentionally separate from the other tools:

- `telegram_jellyfin_bot` downloads and manages the queue.
- `organizer` sorts/renames files for Jellyfin.
- `movie_organizer` safely imports one confirmed movie at a time.
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

To use movie mode, also run `movie_organizer\install.bat` once from the parent
folder and configure the two movie paths described below.

Do not commit or share `config.json`; it contains your token and private settings.

For a complete bilingual walkthrough, see
[HOW_TO_USE.md](HOW_TO_USE.md), or send `/guide` to the running bot and choose
English or فارسی.

## Important config fields

- `bot_token`: your BotFather token.
- `telegram_api_id` and `telegram_api_hash`: values from my.telegram.org.
- `jellyfin_library_path`: your main Jellyfin shows/anime library folder.
- `jellyfin_movie_library_path`: a separate Jellyfin Movies library. Leave it
  empty to disable movie mode.
- `movie_staging_path`: a separate temporary download folder outside both
  Jellyfin libraries. Leave it empty when movie mode is disabled.
- `movie_sorter_command`: command used to call the independent movie organizer.
- `scan_after_movie_import`: automatically start and monitor Jellyfin after a
  movie import or movie undo.
- `allowed_chat_ids`: the Telegram group/channel IDs that may use the bot.
- `confirm_before_download`: if `true`, the bot waits for `/confirm_download`.
- `ask_before_overwrite`: if `true`, the bot asks before handling duplicate files.
- `telegram_download_read_timeout_seconds`: maximum time a large Telegram
  transfer may produce no data before it is marked failed. The default is 1800
  seconds (30 minutes); normal bot requests keep their shorter timeout.
- `sorter_command`: command used to call the independent `organizer` tool.
- `jellyfin_server_url` and `jellyfin_api_key`: needed for `/jellyfin_scan`.
- `jellyfin_scan_poll_interval_seconds`: how often an explicitly requested scan
  is checked (default `5` seconds).
- `jellyfin_scan_monitor_timeout_seconds`: how long the bot waits for completion
  before it stops monitoring (default `3600` seconds). It does not cancel the
  Jellyfin scan.
- `fuzzy_search_command`: command used to call the optional IMDb fuzzy search tool.

For an existing `config.json`, the only new paths you must add to enable movie
mode are these (choose your own real folders):

```json
"jellyfin_movie_library_path": "E:\\Jellyfin\\Movies",
"movie_staging_path": "D:\\TelegramMovieStaging"
```

The shows library, Movies library, and staging folder must be three separate,
non-nested folders. The update process preserves your existing `config.json`,
so it cannot choose these machine-specific paths for you.

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

## Movie mode

Movie mode is independent of the currently selected series folder:

1. Send `/movie_mode`.
2. Send one movie video.
3. Choose **Search using filename** or **Enter name manually**.
4. Select the IMDb result and confirm the exact final folder/file name. If IMDb
   is unavailable after a manual search, confirm the manual title instead.
5. Send `/download`, review the final Movies destination, then send
   `/confirm_download`.

The download first completes in `movie_staging_path`. Only then does the movie
organizer perform an internal dry-run and move it into a folder such as:

```text
Movies\Interstellar (2014) [imdbid-tt0816692]\
  Interstellar (2014) [imdbid-tt0816692].mkv
```

Use `/series_mode` before sending episode files again. A movie import never
overwrites an existing movie video. On conflict, the completed download remains
in staging and `/movie_import ID` can retry it after you fix the problem.

## Commands

- `/menu` — show the button menu.
- `/guide` — choose the English or Persian step-by-step usage guide.
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
- `/recover_current` — manually reconcile incomplete operations in only the current folder.
- `/fix_metadata_current` — manually rename episode NFO/artwork in only the current folder.
- `/sort_latest` — sort the latest downloaded folder.
- `/sort_folder NAME` — sort a specific folder in the library.
- `/sort_status` — show latest sorter run status.
- `/undo_sort_last` — undo the latest sorter batch.
- `/undo_sort_batch ID` — undo a specific sorter batch.
- `/jellyfin_scan` — request a full scan, monitor it, and report when it finishes.
- `/jellyfin_status` — test Jellyfin and show live scan state/progress.
- `/episodes [NAME]` — show known and missing episodes for one series.
- `/library_episodes` — show an episode summary for the whole library.
- `/imdb_search NAME` — fuzzy-search an official IMDb folder name.
- `/imdb_fix_current [NAME]` — safely rename the current folder using IMDb results.
- `/movie_mode` — make newly received videos enter the independent movie flow.
- `/series_mode` — return newly received videos to the series flow.
- `/movie_current` — show the latest movie job and its status.
- `/movie_cancel` — remove the current movie before it is downloaded.
- `/movie_import [ID]` — retry a completed movie that is still in staging.
- `/movie_undo_last` — restore the latest imported movie to staging.
- `/movie_undo_batch ID` — restore a specific movie import batch.
- `/chatid` — show the current chat ID.
- `/help` — show command help and copy buttons.

## Channel command buttons

Telegram channels cannot pre-fill editable slash commands the same way normal chats can. For commands that need extra text, `/help` shows copy buttons. Tap a button, paste the command, then add the folder name or batch ID.

## Persistent category keyboard

In a private chat, group, or supergroup, send `/menu` once to enable a persistent
keyboard beside the message box. Its category buttons open smaller inline menus
for downloads, folders, sorting, undo/recovery, Jellyfin, IMDb, episodes, and
bot information. Movies have their own category. The existing quick-access inline menu is still sent and has
not been replaced.

Telegram does not support persistent reply keyboards in channels. In a channel,
use the **Command categories** button at the bottom of the existing inline menu;
it opens the same categorized submenus.

## Duplicate files

The bot does not overwrite automatically. If a destination file already exists, it asks you to choose:

```text
/resolve 12 skip
/resolve 12 overwrite
/resolve 12 save_with_suffix
```

`save_with_suffix` creates a name like `Video (1).mkv`.
When you explicitly choose `overwrite`, the completed `.part` file is installed
with an atomic replacement. The old destination is not deleted first, so a
failed replacement leaves the existing file in place.
Before any completed `.part` file is published, its byte count must match the
size Telegram reported. A mismatch marks the item as failed, leaves the
`.part` file for inspection/retry, and does not create or replace the final
video.

## Manual current-folder maintenance

`/recover_current` is an on-demand safety check. It scans operation journals
only under the currently selected series folder; it never runs continuously
and never scans good sibling folders.

`/fix_metadata_current` is also manual. It renames only episode `.nfo` and
episode `.jpg`, `.jpeg`, `.png`, or `.webp` sidecars that can be matched to one
video in the same directory. Series/season artwork and NFO files are left alone.

## Jellyfin scan completion

`/jellyfin_scan` triggers the scan and then checks Jellyfin's `RefreshLibrary`
scheduled task only for that requested operation. The bot reports when Jellyfin
accepts the request, when the task starts, progress milestones when available,
and the final `Completed`, `Failed`, or cancelled result.

If the monitoring timeout is reached, the scan is not cancelled. Use
`/jellyfin_status` to see Jellyfin's live task state and progress. No continuous
background polling happens unless a scan command is actively being monitored.

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
- Movie mode is disabled: set both movie paths and run
  `movie_organizer\install.bat` once.
- Movie import stays in staging: use `/movie_current`, fix the reported conflict
  or configuration error, then use `/movie_import ID`.
- `.part` file remains: the download was interrupted; run `/download` again.
