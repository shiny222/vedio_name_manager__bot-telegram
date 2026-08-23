# System design

This document describes how Jellyfin Video Manager is divided, what each
component is allowed to do, and how a Telegram media file becomes an organized
Jellyfin item. It reflects the current code, not a future design proposal.

## Design goals

The system is built around six goals:

1. **Explicit library selection.** A user chooses where media belongs. AI must
   never decide between animation/live action or series/movie libraries.
2. **Low-friction normal use.** Once a chat selects a library, normal use is
   send, review, and confirm. Repeated episodes should not require repeated
   folder selection.
3. **Independent deterministic tools.** Series organization, movie import, and
   IMDb search can each run without the Telegram bot.
4. **No silent destructive action.** Existing media is not overwritten unless
   the user explicitly selects the overwrite policy.
5. **Recoverability.** File moves have history and journals. Downloads have
   persistent queue state and `.part` files.
6. **Optional network intelligence.** n8n/AI and IMDb improve identification,
   but manual operations remain available when either is unavailable.

## Components and responsibilities

| Component | Responsibility | Does not do |
|---|---|---|
| `telegram_jellyfin_bot` | Poll Telegram, render menus, isolate chat state, queue files, build final names, download, coordinate tools, monitor Jellyfin scans | Directly infer official metadata without its identification/search bridges |
| Local Telegram Bot API | Receive Telegram API calls and download large Telegram files into local storage | Organize media or decide destinations |
| n8n filename agent | Convert an untrusted filename/caption into a structured title/season/episode suggestion | Poll Telegram, see the media file, choose a library, or touch the filesystem |
| `fuzzy_search` | Search IMDb for canonical title/year/ID candidates and cache successful exact queries | Move files or make the final user decision |
| `organizer` | Detect episode numbers, create season layout, move/rename videos and subtitles, record history, undo/recover | Guess the series title from the release filename; the parent folder is trusted |
| `movie_organizer` | Import one confirmed staged movie and subtitles into one Jellyfin movie folder; record/undo moves | Search IMDb or download Telegram files |
| Jellyfin bridge | Trigger a library refresh and monitor the requested scheduled task | Install or configure Jellyfin |
| Existing Jellyfin | Scan, identify, store metadata, and serve the media libraries | Participate in Telegram or n8n processing |

## Deployment topology

The NAS deployment uses three independent Compose projects and one existing
Jellyfin project:

```text
Host / NAS
|
|-- Existing Jellyfin Compose project
|   `-- jellyfin container (published port 8096)
|
|-- telegram-bot-api-compose
|   `-- telegram-bot-api container
|
|-- n8n-compose (optional)
|   `-- n8n container
|
`-- video-manager-compose
    `-- video-manager container
```

The three automation containers join the external Docker network
`media-automation`. Jellyfin does not need to join it. The bot reaches the
existing host-published Jellyfin port through `host.docker.internal`, supplied
by Compose's `host-gateway` mapping.

The Local Bot API port is published only on NAS loopback by default. The bot
uses the container DNS name `telegram-bot-api:8081` over the shared network.

## Filesystem model

The bot container has a read-only root filesystem and only these writable
mounts:

- `/app/data` — SQLite state;
- `/app/logs` — bot logs;
- `/app/staging` — completed movies waiting for import or retry;
- `/app/fuzzy_search/data` — IMDb cache;
- `/media/animation-serise` — animated series library;
- `/media/animation-movie` — animated movie library;
- `/media/video-serise` — live-action series library;
- `/media/video-movie` — live-action movie library.

The Local Bot API data directory is mounted into the bot container **read-only**
at the same container path used by the API service. This lets the bot copy a
completed Telegram file without giving it permission to alter Telegram's own
storage.

Compose uses `create_host_path: false` for important bind mounts. A wrong or
missing host media path therefore stops deployment instead of silently creating
an empty directory and writing media to the wrong disk.

## Configuration ownership

Configuration is divided by trust and lifecycle:

- Git tracks code, Dockerfiles, Compose templates, `.env.example` files, and
  the n8n workflow template.
- Runtime `.env` files live beside the deployed Compose files and are ignored
  by Git.
- Windows uses `telegram_jellyfin_bot/config.json`, also ignored by Git.
- n8n credentials remain encrypted in n8n data and are not exported in the
  workflow JSON.
- The bot token, Jellyfin API key, Telegram API hash, webhook secret, and n8n
  encryption key must never be committed.

## Telegram update and chat-state model

The Python bot is the only update consumer. It calls `getUpdates` through the
Local Telegram Bot API. n8n never receives Telegram updates and therefore
cannot compete for the same update offset.

Authorization works at chat level:

- an empty allow-list permits any chat that can reach the bot;
- a populated allow-list accepts only those Telegram chat IDs;
- no separate account or login system exists.

The SQLite store uses WAL mode and a process-local lock. It contains:

- a key/value settings table;
- queue items with Telegram identity, selected library, media type,
  identification result, final download name, status, and errors;
- sorter/import run records with chat owner, operation type, library, folder,
  command, output, and timestamps.

Settings are namespaced by chat ID. These include language, selected library,
current series folder, download confirmation, latest movie/sorter references,
and related workflow state. Queue rows also include `chat_id` and the selected
`library_key` captured when the file arrived.

This means changing a chat's library later does not reroute files already in
its queue. Different private chats do not share state. Members of one group do
share the group's state because Telegram supplies one group chat ID.

Telegram topic identifiers are context only: replies are sent back to the same
existing topic. The bot does not create topics for menus or commands.

## Series workflow

### 1. Library selection

The user selects a configured series library. The bot stores its key for that
chat and sets the chat to series mode. A manual current folder may be empty;
normal AI-assisted use does not require choosing one first.

### 2. Queue registration

When a supported Telegram video/document arrives, the bot records a queue row
using the chat's current library. Duplicate registration is constrained by the
combination of chat, Telegram message, and unique file ID.

The incoming file is not downloaded yet.

### 3. Compact batch identification

Series and movie arrivals are grouped by chat, sender, and library for a short
two-second window. This reduces per-file message spam. Series can share one
result across episodes of the same newly discovered title; movies remain
independent identities inside one compact status.

Within that batch, AI requests are processed sequentially. Sequential calls are
deliberate: they reduce pressure on free/rate-limited model endpoints. Each
result is still routed independently, so a mixed upload can contain episodes
from different series.

The n8n request contains:

- a generated request ID;
- Telegram chat ID;
- authoritative media kind;
- authoritative selected library key;
- filename;
- optional caption.

The AI response is treated as a suggestion and must pass structural validation.
For series it should contain a searchable title and, where identifiable, season
and episode. Invalid, missing, or unavailable responses leave the queue item
undownloaded and expose manual/current-folder fallbacks.

### 4. IMDb and existing-folder matching

The bot sends the suggested title to the independent IMDb fuzzy-search tool.
The top result proposes the Jellyfin folder form:

```text
Official Title (Year) [imdbid-tt1234567]
```

An existing folder is reused automatically only for conservative matches:

- a unique exact IMDb ID;
- an exact expected canonical folder name; or
- a unique normalized title match, with year used to disambiguate duplicates.

Fuzzy score alone does not authorize writing into an existing folder. If a
folder match is not reliable, a new identity requires confirmation. All queued
episodes in the same new-series group share that decision.

If IMDb is unavailable, a unique existing normalized title can still be reused.
Otherwise the bot asks for a manual decision rather than guessing.

### 5. Final naming and download plan

The bot stores the resolved folder, IMDb identity, season, episode, and planned
download name in each queue row. The review shown by `/download` uses the actual
final Jellyfin filename, for example:

```text
Witch Hat Atelier - S01E01.mkv
```

It does not show an internal `Incoming` filename as the final result.

### 6. Download and publish

The downloader asks the Local Telegram Bot API for the local source path, then
copies it into a destination ending in `.part`. When Telegram provides an
expected size, the copied byte count must match before publication.

The `.part` file is renamed into its planned loose destination only after the
copy is complete and verified. If the final path exists, the queue item enters
the conflict flow. `skip` leaves it alone, `save_with_suffix` finds a free name,
and `overwrite` is used only after explicit selection.

### 7. Series organization

After all applicable downloads finish, the bot invokes the standalone series
organizer for each affected folder. The folder name is the trusted series
title. Release filenames are used only for season/episode detection.

The organizer creates `Season NN`, uses Season 01 when only an episode is known,
moves unrecognized files to `_Unsorted`, moves unapproved target-name conflicts
to `_Conflicts`, and brings same-stem subtitles with their video. For an
explicitly approved episode replacement, it matches the existing season/episode
across supported video extensions and older filenames, archives that media,
then installs the new episode in one rollback batch.

Successful automatic sorting is quiet. Full subprocess output is retained in
the sorter run record and can be viewed with `/sort_status`. Failures remain
visible.

### 8. Jellyfin refresh

If `SCAN_AFTER_AI_SERIES_SORT=true`, a successful AI-assisted series sort can
trigger the existing Jellyfin bridge. Otherwise the user can request
`/jellyfin_scan` manually.

If a Jellyfin scan is already running, the bridge first waits for it to finish
and then requests a fresh scan. The fresh request guarantees that files moved
into their final folders near the end of the older scan are checked.

## Movie workflow

### 1. Library and identification

Selecting a movie library switches the chat to movie mode. Each incoming movie
is an independent queue job, while a short burst shares one status message. AI
proposes title/year from the filename. The top IMDb result is accepted
automatically only when the normalized title agrees exactly, the score is high,
and the AI/IMDb years agree. If the source filename contains one unambiguous
release year, that year must agree too. Ambiguous titles, year mismatches, and
weak results remain awaiting an explicit IMDb/manual choice.

### 2. Planned name

The final base name includes title, optional year, and optional IMDb ID:

```text
Interstellar (2014) [imdbid-tt0816692]
```

The review shows the final movie-library filename even though the download will
first go to staging.

Before download, the bot checks the canonical target folder/IMDb ID and active
queue identities. If a final movie video already exists or the same movie is
already pending, the item enters `waiting_overwrite` and displays the incoming
and conflicting filenames. Cancel leaves the old item untouched. Replace marks
only the selected queue item with `replace_library`; the independent organizer
still rechecks the destination when it executes.

### 3. Staged download

Each movie queue item has its own staging job directory such as
`/app/staging/movies/job-37`. The Local Bot API copy and `.part` verification
rules are the same as for series.

### 4. Import

After a complete download, the bot calls the independent movie organizer. It
first performs a dry-run internally. The real import creates the final movie
folder and moves the video plus matching subtitle sidecars. Automatic imports
are summarized once per download batch; internal dry-run and per-file success
details remain in logs/state instead of being posted as repeated chat messages.

Without a stored replacement approval, an existing destination is never
changed. With approval, the old media is archived before the import rather than
overwritten in place. If import fails, the completed movie stays in staging and
`/movie_import ID` retries only that job after the cause has been corrected.

### 5. Undo

Movie undo moves the imported files back to their original staging paths after
checking file size and ensuring those paths are free. The movie folder history
record becomes `undone` after a successful restore.

## Naming model

### Series folders

Canonical folder identity may include year and provider ID:

```text
Series Title (Year) [imdbid-tt1234567]
```

### Episode files

Provider identity and year are removed from the filename:

```text
Series Title - S01E02.mkv
```

The series organizer supports explicit patterns such as `S01E02`, `1x02`,
`Episode 02`, `Ep 02`, `E02`, Persian/Arabic season-episode forms, Japanese
episode notation, Korean episode notation, and carefully filtered isolated
numbers. Common resolutions, years, codecs, and ambiguous multiple numbers are
rejected as isolated episode guesses.

### Movies

The folder and primary file share the same canonical base:

```text
Movie Title (Year) [imdbid-tt1234567]/
Movie Title (Year) [imdbid-tt1234567].mkv
```

Extensions are preserved. Matching subtitles follow their primary video.

## Safety model

### Path containment

Folder and filename input is sanitized and resolved under a configured library
root. The path guard rejects attempts to escape that root. Docker host mounts
also define the maximum writable media scope.

### No-overwrite policy

Normal organizer/import/undo actions do not replace an existing path. A final
library replacement requires an explicit per-item decision. The organizer moves
the old video and subtitle sidecars into the hidden
`.replacement_backups/BATCH_ID` path, records those moves, and then installs the
new media. Undo moves the new media back first and restores the archived media.
Loose download destinations retain the separate atomic `.part` overwrite flow.

### Download integrity

Incomplete data remains in a `.part` file. The expected Telegram size is
verified when possible. A mismatch leaves the part for inspection/retry and
does not publish a final file.

### Move history

Every real organizer move appends a JSON record containing timestamp, original
and new full paths, both filenames, byte count, file type, status, and batch ID.
History is written in the destination season/movie folder or the owning series
folder for unsorted/conflict items.

### Operation journal

Before important moves, the organizer writes and flushes a planned event to an
append-only JSONL journal. It then verifies paths and size and records verified
and completed phases. If saving normal history fails after a move, the code
immediately attempts to move the file back and records the outcome.

The journal is evidence for repair; it is not a second video copy.

### Explicit recovery

`recover-folder`/`/recover_current` checks only one selected series folder and
only when requested. It can reconcile a journaled operation when exactly one
verified copy exists. Ambiguous states are reported and left untouched.

### Undo validation

Undo checks that:

- the new/current file exists;
- the original destination is free;
- file size matches when recorded;
- the record is still active.

Unsafe records are skipped. A partially successful batch returns failure so the
bot cannot report a full rollback inaccurately.

## Failure behavior

| Failure | Result |
|---|---|
| n8n unavailable/invalid JSON | Queue item remains undownloaded; manual/current-folder fallback is offered |
| IMDb unavailable | Cached exact query may work; otherwise manual title flow remains |
| Telegram read timeout | Item is marked failed and `.part` is retained when present |
| Size mismatch | Final file is not published |
| Existing destination | Item waits for explicit conflict policy or organizer routes it to `_Conflicts` |
| Movie import failure | Completed file stays in staging for `/movie_import ID` |
| Sorter failure | Run output is stored; user checks `/sort_status` |
| Jellyfin monitor timeout | Scan is not cancelled; `/jellyfin_status` can inspect current state |
| Bot restart during download | Database changes `downloading` rows back to `queued` with a recovery note |
| Power loss during organizer move | Explicit per-folder recovery reads the durable journal |

## Concurrency

The default Docker configuration uses `MAX_PARALLEL_DOWNLOADS=1`. Background
tasks are tracked in memory so failures are logged rather than disappearing as
unobserved fire-and-forget exceptions. Filesystem-sensitive operations are
serialized and still depend on no-overwrite/path guards for correctness.

Series AI calls within one burst are intentionally sequential. Multiple chats
have independent logical state, but they share the same physical libraries and
external services.

## Non-goals

The current project is not:

- a media request/accounting system;
- Sonarr or Radarr;
- a metadata provider for Jellyfin;
- a continuous filesystem watcher;
- a replacement for Jellyfin library configuration;
- an AI agent with unrestricted NAS access;
- a backup system for the media content itself.

It coordinates user-selected downloads and safe naming/organization on top of
an existing Jellyfin installation.
