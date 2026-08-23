# Operations, updates, backup, and recovery

This runbook covers routine operation after the first deployment. The exact
initial NAS directory creation and Compose start commands are in
[NAS Docker setup](../nas/README.md).

## Start and stop order

Start the Local Telegram Bot API first, optional n8n second, and the Python bot
last:

```bash
cd /path/to/jellyfin-compose/telegram-bot-api-compose
docker compose up -d

cd ../n8n-compose
docker compose up -d

cd ../video-manager-compose
docker compose up -d
```

n8n may be omitted when `N8N_AGENT_ENABLED=false`. Jellyfin can already be
running and does not need to be restarted.

Stop the Python bot before the Local Bot API so no new API/download request is
accepted during shutdown:

```bash
cd /path/to/jellyfin-compose/video-manager-compose
docker compose stop

cd ../telegram-bot-api-compose
docker compose stop
```

Stopping Video Manager does not stop Jellyfin.

## Health checks

### Container state

Run `docker compose ps` inside each stack directory. The Local Bot API should
become healthy. Video Manager and n8n should remain `Up` without restart loops.

### Local Telegram Bot API and Jellyfin

From the NAS host:

```bash
curl http://127.0.0.1:8081
curl http://127.0.0.1:8096/System/Info/Public
```

An HTTP response on 8081 proves that the Local Bot API port is listening; a
Telegram method call also needs the BotFather token. Keep port 8081 on NAS
loopback. Inside Telegram, `/jellyfin_status` tests the configured authenticated
Jellyfin bridge and reports the scan task state.

### Shared Docker network

```bash
docker network inspect media-automation
```

It should include `telegram-bot-api`, `video-manager`, and n8n when the optional
n8n stack is running.

### Media write permission

Test every mounted library, not just one:

```bash
cd /path/to/jellyfin-compose/video-manager-compose
docker compose exec -T video-manager sh -c '
id
for p in /media/animation-serise /media/animation-movie /media/video-serise /media/video-movie /app/staging /app/data /app/logs; do
  if test -w "$p"; then echo "WRITE_OK $p"; else echo "WRITE_DENIED $p"; fi
done
'
```

This check does not create a test file. If it says `WRITE_DENIED`, inspect the
corresponding host directory:

```bash
stat -c 'owner=%u group=%g permissions=%A path=%n' /actual/host/library/path
getfacl /actual/host/library/path
grep -E '^(PUID|PGID)=' .env
```

Set the bot process to a UID/GID already allowed by the NAS ownership and ACL.
For the current `nobody:users` folders, `PGID=100` is the important group. Do
not apply recursive `chmod 777` or change ownership across the media archive as
a first response; that can damage NAS ACL expectations.

After changing `PUID` or `PGID`, recreate without rebuilding:

```bash
docker compose up -d
```

## Logs and bot-visible status

View recent or live service logs:

```bash
cd /path/to/jellyfin-compose/video-manager-compose
docker compose logs --tail=200 video-manager
docker compose logs -f video-manager
```

Use the same pattern for `telegram-bot-api` and `n8n`. Press `Ctrl+C` to leave
`logs -f`; the container keeps running. Docker rotates each service's JSON logs
at 10 MB and retains five files.

Bot commands provide focused diagnostics:

- `/status` — queue counts, incomplete `.part` files, tracked tasks;
- `/queue` — current chat's active queue;
- `/sort_status` — latest series organizer command and retained output;
- `/movie_current` — latest movie job and staging/import state;
- `/jellyfin_status` — connection and current scan task;
- `/folder` — current manual series folder;
- `/libraries` — configured libraries and current selection.

Successful automatic sort output is intentionally not posted as a long chat
message. It remains available in `/sort_status` and logs.

## Safe update procedure

### Decide whether a rebuild is needed

| Change | Rebuild image? | Recreate container? |
|---|---:|---:|
| `.env` value only | No | Yes |
| Python source | Yes | Yes |
| Python requirements | Yes | Yes |
| `Dockerfile` | Yes | Yes |
| Compose template | Usually no | Yes after synchronizing |
| n8n workflow edited in UI | No | No; publish/activate in n8n |

### Update code on NAS

Inspect the source clone first:

```bash
cd /path/to/jellyfin-compose
git -C source status --short
git -C source branch --show-current
```

Do not pull over unexplained tracked changes. When clean:

```bash
git -C source pull --ff-only
bash source/nas/install-or-update.sh "$PWD"
```

The synchronization script:

- verifies the committed Linux Local Bot API checksum when `sha256sum` exists;
- copies the three tracked Compose templates into sibling stack folders;
- refreshes deployed `.env.example` files with real absolute paths;
- creates a real `.env` only if it does not exist;
- creates missing persistent directories;
- applies n8n data ownership when run as root;
- creates the external network when absent.

It does not pull Git itself, start containers, overwrite `.env`, or modify the
existing Jellyfin Compose project.

Rebuild and recreate the Python bot:

```bash
cd video-manager-compose
docker compose config
docker compose build --pull
docker compose up -d
docker compose logs --tail=100 video-manager
```

`docker compose config` can display resolved environment values. Redact tokens,
API hashes, API keys, webhook secrets, and encryption keys before sharing its
output.

Rebuild Local Bot API only when its binary, checksum, Dockerfile, or image
runtime changed:

```bash
cd ../telegram-bot-api-compose
docker compose config
docker compose build --pull
docker compose up -d
```

Pull n8n separately only when you deliberately want to update n8n:

```bash
cd ../n8n-compose
docker compose pull
docker compose up -d
```

Pin `N8N_IMAGE` and read n8n release notes before major upgrades.

### Roll back a code deployment

Persistent data is outside the image, so rebuilding an earlier Git revision
does not normally erase queue/history state. Record the known-good commit before
an upgrade:

```bash
git -C source rev-parse HEAD
```

If the new image fails, save logs and current state first. Then check out a
known-good commit in the source clone, rebuild the affected image, and recreate
its container. Do not downgrade n8n data or SQLite schema blindly; code rollback
is safest over a backup made immediately before the update.

### Update Windows

Stop `run.bat` and `run_local_bot_api.bat`, then run the root `update.bat`.
It requires a Git clone, refuses tracked local modifications, pulls fast-forward
only, and refreshes requirements in virtual environments that already exist.
It preserves ignored `config.json`, state, logs, staging, tools, and `.venv`
directories.

## Backup procedure

### What to back up

| Data | Why it matters |
|---|---|
| `video-manager-compose/.env` | bot token, library paths, behavior settings |
| `video-manager-compose/data/` | queue, chat settings, sorter/import ownership, IMDb cache |
| `video-manager-compose/staging/` | movie jobs waiting for import or retry |
| `video-manager-compose/logs/` | optional diagnostics |
| `telegram-bot-api-compose/.env` | Telegram application credentials |
| `telegram-bot-api-compose/data/` | Local Bot API local state/cache |
| `n8n-compose/.env` | n8n URL/version and encryption key |
| `n8n-compose/data/` | workflows, credentials, executions, n8n database |
| organizer history/journals in media folders | move audit, undo, power-loss recovery |
| source Git commit ID | identifies the exact code version |

Media content needs its own backup strategy; organizer history is not a copy of
the video.

### Consistent backup

1. Note the current source commit.
2. Let active downloads/imports finish or cancel them safely.
3. Stop Video Manager before copying its SQLite files.
4. Stop n8n before copying its data directory.
5. Copy the listed directories to separate storage.
6. Start n8n and Video Manager again.
7. Verify `/status` and `/jellyfin_status`.

Stopping avoids a mismatched `state.db`, `state.db-wal`, and `state.db-shm`
backup. If all three SQLite files exist, keep them together.

### Restore

1. Stop the affected stack.
2. Keep a copy of the current broken state before replacing anything.
3. Restore `.env` and the matching persistent directory from one backup.
4. Restore the original n8n encryption key with n8n data.
5. Restore code at the recorded compatible commit if necessary.
6. Rebuild the image when code was restored.
7. Start dependency services first and inspect logs.
8. Verify mounts and commands before downloading new media.

Do not restore a live Windows SQLite database into Linux while either bot is
running. Old organizer history with Windows drive-letter paths cannot be safely
undone in Linux without a deliberate path migration.

## File-operation rollback

### Series

The bot offers:

- `/sort_history` — current folder's numbered revisions;
- `/sort_back` — undo the newest applied revision;
- `/sort_forward` — redo the next undone revision;
- `/undo_sort_last` — latest active series batch owned by the chat;
- `/undo_sort_batch BATCH_ID` — a specific batch;
- `/recover_current` — reconcile journals only under the selected folder.

The standalone organizer also supports `undo-last`, `undo-batch`,
`undo-folder`, `sort-back`, `sort-forward`, and `recover-folder`.

### Movies

- `/movie_undo_last` restores the latest imported movie to staging;
- `/movie_undo_batch BATCH_ID` restores a selected import;
- `/movie_import ID` retries a completed staged file after fixing a failure.

Undo never overwrites a path that already exists. Resolve the blocking file and
retry instead of deleting history or editing JSON status by hand.

## Power-loss and interrupted-operation response

### Interrupted Telegram download

1. Start the bot and use `/status`.
2. Database rows left as `downloading` are changed back to `queued` with a
   recovery note during startup.
3. Inspect the reported `.part` file.
4. Use `/download` again. Do not rename a partial file into the final name.

### Interrupted series move

1. Select the affected library and exact series folder.
2. Use `/recover_current` once.
3. Review whether the operation was recovered, needed no change, or remained
   ambiguous.
4. For ambiguity, inspect both paths and byte counts manually; do not delete
   the journal before diagnosis.

Recovery is not a continuous scanner. It reads journals only inside the folder
explicitly selected by the user.

### Interrupted movie import

1. Use `/movie_current`.
2. If the completed source remains in staging, correct the reported path,
   permission, or destination conflict.
3. Use `/movie_import ID` to retry.
4. If some moves completed, consult the movie folder's history and journal
   before manual changes.

## Troubleshooting by symptom

### Bot does not react to a private message

- confirm the Python bot container is running;
- inspect logs for token/API errors;
- confirm only one bot polling process is running for the token;
- check `ALLOWED_CHAT_IDS`;
- use a supported video extension.

### Bot does not react in a group

- add the bot to the group;
- disable BotFather privacy mode if it must receive ordinary media messages, or
  make it an administrator with appropriate access;
- verify the group chat ID is allowed;
- remember that all members of one group share group queue/settings.

### Bot does not see channel posts

- add it as channel administrator;
- allow the channel's negative chat ID;
- channels use inline menus because Telegram does not support persistent reply
  keyboards there.

### Local Bot API returns unauthorized/401

- verify the BotFather token;
- confirm the bot is not active through an incompatible old Local Bot API
  session;
- verify Telegram application ID/hash in the API stack;
- stop the old Windows Local Bot API before moving the same bot to NAS.

### Telegram download times out

- inspect Local Bot API and Video Manager logs;
- check free space on Local Bot API data, staging, and destination volumes;
- keep `TELEGRAM_DOWNLOAD_READ_TIMEOUT_SECONDS` large enough for slow files;
- retry from `/download`; the failed `.part` remains for diagnosis;
- do not assume a movie was imported when the queue says `0 completed`.

### `Permission denied` under `/media/...`

- run the non-writing permission test above;
- compare container `id` with host `stat`/ACL output;
- set the appropriate `PGID` (current NAS media group is 100);
- recreate Video Manager;
- do not rebuild solely for `PUID`/`PGID` changes.

### n8n returns webhook 404

The production webhook is not registered. Confirm:

- the bot URL ends in `/webhook/media-identify`;
- the workflow is published/active;
- the Webhook node uses `POST` and path `media-identify`;
- the bot is not using the browser `/workflow/...` editor URL;
- for test mode only, use the test URL while **Listen for test event** is active.

### n8n says response must contain one JSON identification object

Open the n8n execution and inspect **Parse and Normalize Response** and
**Return Identification**. The final response must be one object, not an array,
Markdown code fence, text wrapper, or full n8n item envelope. The Webhook node
must respond through **Respond to Webhook**, and that node must receive the
normalized object.

### AI credential succeeds but the model call fails

A successful credential test proves endpoint authentication, not model
availability or credit. Common causes are unavailable provider capacity,
insufficient credits, a wrong model ID, or a rate-limited free model. Select an
available model in the n8n chat-model node and configure reasonable retry there.
The bot leaves the item undownloaded when identification fails.

### Movie downloaded but import failed

The normal flow checks known library and queue identities before transfer. When
it finds one, verify the incoming and existing filenames in the prompt. Choose
Replace only if both truly represent media that should occupy the same Jellyfin
movie/episode identity; otherwise cancel and correct the IMDb/manual identity.
An approved replacement archives the old media and remains undoable.

A failure after download usually means the destination changed during the
transfer, an older staged job predates the check, or the filesystem/tool failed
for another reason.

- use `/movie_current` to find the queue ID and staging path;
- fix the permission, organizer path, or destination conflict;
- ensure the movie organizer exists inside the current bot image;
- retry with `/movie_import ID`;
- do not download the same large movie again while a verified staged copy
  exists.

### Series file goes to `_Unsorted`

The organizer could not find a reliable episode number. Use a recognized form
such as `S01E02`, `1x02`, `Episode 02`, or an isolated safe episode number. If
AI already identified it, inspect `/sort_status` and the queue's final name.

### Posters or metadata do not appear

The bot can trigger a Jellyfin scan but is not a metadata provider. Verify the
Jellyfin library type, metadata provider configuration, provider network/API
access, folder identity, permissions, and Jellyfin logs. An `[imdbid-...]` tag
can improve identity but cannot make an unavailable provider fetch artwork.

## Incident evidence checklist

Before changing files manually, collect:

- time of the operation;
- Telegram chat and queue ID;
- selected library key and folder;
- `/status`, `/sort_status`, or `/movie_current` output;
- relevant Video Manager, Local Bot API, n8n, and Jellyfin logs;
- source/destination existence and byte counts;
- `.rename_history.json` and the matching operation journal;
- current Git commit and image creation time;
- current `.env` values with secrets redacted.

This evidence normally distinguishes identification, download, permission,
organizer, and Jellyfin-scan failures without risking more changes.
