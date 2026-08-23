# Telegram Jellyfin Bot

This bot watches an allowed Telegram group or channel, queues video files,
downloads them only after your command/confirmation, and can optionally call
the separate series organizer, movie organizer, and IMDb fuzzy-title tool.

The bot is intentionally separate from the other tools:

- `telegram_jellyfin_bot` downloads and manages the queue.
- `organizer` sorts/renames files for Jellyfin.
- `movie_organizer` safely imports one confirmed movie at a time.
- `fuzzy_search` is optional and only helps suggest official folder names.

For the complete project architecture, Docker/NAS deployment, every
configuration variable, updates, backups, and recovery, start at the root
[`README.md`](../README.md). This file focuses on the bot itself.

## Deployment choices

### NAS/Docker (recommended)

The root Docker image contains the Python bot plus the three independent helper
tools. Local Telegram Bot API and optional n8n run as separate Compose projects;
the existing Jellyfin installation remains separate and unchanged. Follow
[`nas/README.md`](../nas/README.md), not the Windows steps below.

### Windows requirements

- Windows
- Python 3.10 or newer
- A Telegram bot token from [BotFather](https://t.me/BotFather)
- `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org)
- the included Windows Local Telegram Bot API executable and DLLs under `tools`

## Windows quick install

1. Open the `telegram_jellyfin_bot` folder.
2. Run `install.bat`.
3. Confirm the supplied `tools\telegram-bot-api.exe` and DLLs are present, or
   set another real executable path in `config.json`.
4. Edit `config.json`.
5. Start `run_local_bot_api.bat`.
6. Start `run.bat` in a second window.

To use movie mode, also run `movie_organizer\install.bat` once from the parent
folder and configure the two movie paths described below.

Do not commit or share `config.json`; it contains your token and private settings.

For a complete bilingual walkthrough, see
[HOW_TO_USE.md](HOW_TO_USE.md), or send `/guide` to the running bot and choose
English or فارسی.

## Interface language

Send `/language` and choose **English** or **فارسی**. The selection is stored
per chat in SQLite and is reused after a restart. Menus, buttons, normal status
messages, movie/download prompts, and common errors follow the selected
language. Filenames, paths, command names, provider responses, and technical
exception details are preserved exactly so troubleshooting information is not
damaged.

## Important config fields

- `bot_token`: your BotFather token.
- `telegram_api_id` and `telegram_api_hash`: values from my.telegram.org.
- `jellyfin_library_path`: your main Jellyfin shows/anime library folder.
- `jellyfin_movie_library_path`: a separate Jellyfin Movies library. Leave it
  empty to disable movie mode.
- `media_libraries`: optional named destinations for installations with more
  than one series or movie root. Docker configures the four NAS libraries from
  environment variables automatically.
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

If `allowed_chat_ids` is empty, every chat that can reach the bot is allowed. No
chat ID, registration, password, or login is required. Telegram supplies the
chat ID automatically and the bot uses it only as an internal namespace.

Each chat has independent language, media mode, current folder, queue,
confirmation state, latest-download references, task status, and owned undo
batches. Its selected media library is independent too, and every queued item
keeps the library selected when that item arrived. A private chat is separate
for each Telegram user. Members of the same
group share one group-chat namespace because Telegram gives the group one chat
ID. The Jellyfin folders themselves are still a shared library, so filesystem
operations are serialized and retain the no-overwrite protections.

In chats with Telegram topics enabled, every reply stays in the same existing
topic where the command, button, or file was sent. The bot does not create a
new topic for the category menu.

To restrict access later, add the desired IDs to `allowed_chat_ids`; `/chatid`
shows the current one.

## Two usage workflows

The main menu separates routine use from maintenance:

- **Normal, AI assisted:** choose one of the four libraries once, send one or
  more videos, review the final saved filenames, then use
  **Downloads → Download → Confirm**. The chosen library persists for that
  chat until explicitly changed, and AI is never allowed to select it. A short
  burst of episodes or movies is handled as one compact batch. Mixed series are
  routed independently; an existing reliable series match is automatic, while
  a new series asks for one shared confirmation. An exact high-confidence movie
  title/year match is automatic; uncertain or inconsistent matches still ask.
- **Advanced, no AI required:** manually select or create series folders,
  correct IMDb identity, sort/resort episodes, repair metadata, resolve queue
  conflicts, and use history/undo/recovery tools.

The n8n identification workflow is tracked in `nas/n8n-workflows`. Enable its
bot-side client with `N8N_AGENT_ENABLED`, `N8N_AGENT_URL`, and the optional
shared header secret. If the webhook fails, the queued item remains
undownloaded and the existing manual fallback remains available.
The detailed English and Persian instructions are in `HOW_TO_USE.md` and inside
Telegram under `/guide`.

## Movie mode

Movie mode is independent of the currently selected series folder:

1. Send `/libraries` and choose the correct movie library. `/movie_mode` opens
   only the movie-library choices.
2. Send one movie or a short burst of movies.
3. With AI enabled, exact high-confidence IMDb title/year matches are queued
   automatically and shown in one summary. If the title/year is ambiguous or
   inconsistent, select the correct result or enter the name manually. A clear
   year in the incoming filename must also match before automatic acceptance.
4. If that identity already exists in the selected movie library or queue, the
   bot shows the incoming filename and existing/queued file, then asks
   **Replace existing** or **Cancel download** before any transfer. If the same
   IMDb ID already uses an older folder spelling, replacement targets that
   existing folder instead of creating a second IMDb-identical folder.
5. Send `/download`, review the final Movies destination, then send
   `/confirm_download`. Each line has a temporary batch ID. It stays attached
   to the same file while that batch is reviewed. Press **Remove one item**
   (or send `/remove`), reply with the ID, and reopen `/download` to remove one
   mistaken match without clearing the remaining queue. `/cancel` exits the
   prompt safely. After confirmation starts the batch, the next batch begins
   again at `#1`. Removing one item leaves the other IDs unchanged, so an ID
   never starts pointing to a different file during the same review.

The download first completes in `movie_staging_path`. Only then does the movie
organizer perform an internal dry-run and move it into a folder such as:

```text
Movies\Interstellar (2014) [imdbid-tt0816692]\
  Interstellar (2014) [imdbid-tt0816692].mkv
```

Use `/series_mode` before sending episode files again. Automatic multi-movie
imports produce one compact result. An approved replacement archives the old
video/subtitles under `.replacement_backups/BATCH_ID`, records the archive and
new import in `.rename_history.json`, and can be reversed with movie undo. A
race or filesystem conflict discovered after transfer leaves the completed
file in staging, where `/movie_import ID` can retry it after you fix the problem.

## Commands

- `/start` — choose a language on first use or reopen help/menu.
- `/menu` — show the button menu.
- `/guide` — choose the English or Persian step-by-step usage guide.
- `/language` — choose and remember English or Persian for this chat.
- `/libraries` — choose one of the configured series/movie libraries.
- `/use_library KEY` — select a configured library directly by key.
- `/setfolder NAME` — set a destination folder, using optional IMDb fuzzy search first.
- `/folders` — choose from existing Jellyfin folders.
- `/usefolder NAME` — use an existing folder by exact name.
- `/renamefolder NAME` — safely rename the current folder.
- `/folder` — show the current folder.
- `/unsetfolder` — clear the current folder.
- `/queue` — show queued files.
- `/remove` — ask for a temporary number from the latest `/download` review.
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
- `/movie_mode` — choose a movie library and enter the movie flow.
- `/series_mode` — choose a series library and enter the episode flow.
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
keyboard beside the message box. The main keyboard intentionally contains only
**Downloads**, **Episodes**, **Jellyfin**, **Bot**, **Choose Library**, and
**Advanced**. Choose Library opens the four configured destinations directly.
Advanced contains the less-frequent folder, sorting, undo/recovery, IMDb,
series-workflow, and movie-workflow menus. Commands remain available by slash
command even when their buttons are under Advanced.

Telegram does not support persistent reply keyboards in channels. In a channel,
`/menu` sends the same small main menu as inline buttons.

## Duplicate files

The bot does not overwrite automatically. Final Jellyfin movie and episode
identity collisions use inline **Replace existing** and **Cancel download**
buttons. The prompt identifies both the incoming release filename and the
existing Jellyfin/queued file. Replacement approval is stored on only that
queue item and is rechecked before transfer.

Loose download-path conflicts still support the advanced command form:

```text
/resolve 12 skip
/resolve 12 overwrite
/resolve 12 save_with_suffix
```

`save_with_suffix` creates a name like `Video (1).mkv`.
When you explicitly choose `overwrite`, the completed `.part` file is installed
with an atomic replacement. The old destination is not deleted first, so a
failed replacement leaves the existing file in place.
This atomic loose-file policy is separate from final-library replacement. A
final-library replacement is performed by the organizer with a rollback backup,
not by deleting or atomically overwriting the Jellyfin file.
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
scheduled task only for that requested operation. The bot edits one status
message as the task progresses. On success it edits that same message to
`✅ Jellyfin is ready.` and keeps it visible. Failures remain visible too.

If Jellyfin is already scanning when this request begins, the bot waits for
that older task to finish and then submits one fresh refresh. This prevents a
scan that started before the latest import from missing the newly organized
files.

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
