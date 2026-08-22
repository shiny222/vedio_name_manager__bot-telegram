# Configuration reference

The bot supports two configuration modes:

- **Docker/NAS:** environment variables from
  `video-manager-compose/.env`; Compose sets
  `VIDEO_MANAGER_CONFIG_MODE=env`.
- **Windows/local:** `telegram_jellyfin_bot/config.json` created from
  `config.example.json`.

The two modes produce the same internal configuration. Docker additionally
provides four fixed container mount paths and automatically builds commands for
the sibling Python tools.

## Secret-handling rules

Never commit or share these values:

- `BOT_TOKEN` / `bot_token`;
- `TELEGRAM_API_HASH` / `telegram_api_hash`;
- `JELLYFIN_API_KEY` / `jellyfin_api_key`;
- `N8N_AGENT_SECRET` / `n8n_agent_secret`;
- `N8N_ENCRYPTION_KEY`;
- AI provider API keys stored in n8n Credentials.

Real `.env` and `config.json` files are ignored by Git. The tracked
`.env.example` and `config.example.json` contain placeholders only.

If a bot token or API key is accidentally published, replace it at the issuing
service; removing it from the newest Git commit does not remove it from Git
history.

## Video Manager Docker environment

Edit:

```text
jellyfin-compose/video-manager-compose/.env
```

### Image and process identity

| Variable | Default | Meaning |
|---|---|---|
| `VIDEO_MANAGER_SOURCE_ROOT` | generated absolute clone path | Docker build context containing the root `Dockerfile` |
| `VIDEO_MANAGER_IMAGE_TAG` | `latest` | Tag for the locally built image |
| `PUID` | `0` | UID used by the bot process |
| `PGID` | `0` | GID used by the bot process |

The Compose `user:` setting overrides the `USER 1000:1000` default inside the
image. Choose an identity that can read Local Bot API data and write every
mounted media/staging/data/log directory.

On the current NAS the media directories are owned by `nobody:users`
(`65534:100`). Using `PUID=0` and `PGID=100` gives the container the correct
media group while retaining the deployment's current UID choice. Because the
container drops all Linux capabilities, UID 0 inside the container does not
automatically bypass NAS ACL or permission checks. Always test the actual mount.

### Telegram bot access

| Variable | Default | Meaning |
|---|---|---|
| `BOT_TOKEN` | placeholder | BotFather token; required |
| `ALLOWED_CHAT_IDS` | empty | Comma, semicolon, or whitespace-separated Telegram chat IDs |
| `ALLOWED_VIDEO_EXTENSIONS` | `.mp4,.mkv,.avi,.mov,.webm,.m4v` | Files that can enter the queue |

An empty allow-list means any private chat, group, or channel that can reach the
bot is accepted. This is convenient but gives every reachable chat access to
downloads and filesystem operations. `/chatid` displays the ID for a private
chat, group, or channel.

Example restriction:

```env
ALLOWED_CHAT_IDS=123456789,-1004446532115
```

### Media libraries

| Variable | Default container path | Type |
|---|---|---|
| `LIBRARY_ANIMATION_SERIES_PATH` | `/media/animation-serise` | series |
| `LIBRARY_ANIMATION_SERIES_NAME` | `Animation Series` | display label |
| `LIBRARY_ANIMATION_MOVIE_PATH` | `/media/animation-movie` | movie |
| `LIBRARY_ANIMATION_MOVIE_NAME` | `Animation Movies` | display label |
| `LIBRARY_VIDEO_SERIES_PATH` | `/media/video-serise` | series |
| `LIBRARY_VIDEO_SERIES_NAME` | `Video Series` | display label |
| `LIBRARY_VIDEO_MOVIE_PATH` | `/media/video-movie` | movie |
| `LIBRARY_VIDEO_MOVIE_NAME` | `Video Movies` | display label |
| `DEFAULT_LIBRARY_KEY` | `animation_series` | initial/fallback selection |

Valid default keys in the current four-library environment are:

- `animation_series`
- `animation_movies`
- `video_series`
- `video_movies`

The Compose file deliberately overrides the four `*_PATH` variables with
container paths. The host paths are configured separately for bind mounts:

| Variable | Meaning |
|---|---|
| `HOST_ANIMATION_SERIES_PATH` | Existing host animated-series directory |
| `HOST_ANIMATION_MOVIE_PATH` | Existing host animated-movie directory |
| `HOST_VIDEO_SERIES_PATH` | Existing host live-action series directory |
| `HOST_VIDEO_MOVIE_PATH` | Existing host live-action movie directory |

For the current NAS:

```env
HOST_ANIMATION_SERIES_PATH=/srv/dev-disk-by-uuid-e5048a2d-8521-41d1-8efc-880e999ecc6f/Archive/VIDEO_ARCHIVE/jellyfin/animation-serise
HOST_ANIMATION_MOVIE_PATH=/srv/dev-disk-by-uuid-e5048a2d-8521-41d1-8efc-880e999ecc6f/Archive/VIDEO_ARCHIVE/jellyfin/animation-movie
HOST_VIDEO_SERIES_PATH=/srv/dev-disk-by-uuid-e5048a2d-8521-41d1-8efc-880e999ecc6f/Archive/VIDEO_ARCHIVE/jellyfin/video-serise
HOST_VIDEO_MOVIE_PATH=/srv/dev-disk-by-uuid-e5048a2d-8521-41d1-8efc-880e999ecc6f/Archive/VIDEO_ARCHIVE/jellyfin/video-movie
```

Do not replace those host paths with `/media/...`; `/media/...` exists only
inside the Video Manager container.

### Persistent bot paths

| Variable | Default | Meaning |
|---|---|---|
| `DATA_PATH` | `/app/data` | SQLite database directory |
| `LOGS_PATH` | `/app/logs` | application logs |
| `MOVIE_STAGING_PATH` | `/app/staging/movies` | completed movie downloads before import |
| `TELEGRAM_BOT_API_DATA_PATH` | generated sibling stack path | host directory also mounted by Local Bot API |

The series and movie libraries and staging directory must be different. Movie
staging must not be inside a final media library. Runtime data directories are
bind-mounted, so replacing the image does not erase them.

### Download and organizer behavior

| Variable | Default | Meaning |
|---|---:|---|
| `TELEGRAM_DOWNLOAD_READ_TIMEOUT_SECONDS` | `1800` | Maximum time a large Telegram copy can make no read progress |
| `MAX_PARALLEL_DOWNLOADS` | `1` | Concurrent download worker limit |
| `CONFIRM_BEFORE_DOWNLOAD` | `true` | Require review/confirmation before starting |
| `ASK_BEFORE_OVERWRITE` | `true` | Stop a queue item for conflict policy when destination exists |
| `DEFAULT_TARGET_FOLDER` | empty | Optional legacy/manual default series folder |
| `SORTER_TIMEOUT_SECONDS` | `1800` | Maximum subprocess time for a series organizer command |
| `MOVIE_SORTER_TIMEOUT_SECONDS` | `1800` | Maximum subprocess time for a movie organizer command |
| `FUZZY_SEARCH_TIMEOUT_SECONDS` | `20` | Maximum IMDb helper subprocess time |

Keep both confirmation values enabled for normal safe operation. Increasing
parallel downloads can increase disk and Telegram API load; it does not change
per-chat isolation or overwrite safety.

### Jellyfin integration

| Variable | Default | Meaning |
|---|---:|---|
| `JELLYFIN_SERVER_URL` | `http://host.docker.internal:8096` | Existing Jellyfin base URL as seen from the bot container |
| `JELLYFIN_API_KEY` | empty | Jellyfin API key; scan/status integration is disabled without it |
| `JELLYFIN_REQUEST_TIMEOUT_SECONDS` | `30` | Timeout for ordinary Jellyfin API requests |
| `JELLYFIN_SCAN_POLL_INTERVAL_SECONDS` | `5` | Interval while monitoring an explicitly requested scan |
| `JELLYFIN_SCAN_MONITOR_TIMEOUT_SECONDS` | `3600` | Stop monitoring after this time; does not cancel Jellyfin's task |
| `SCAN_AFTER_MOVIE_IMPORT` | `true` | Trigger scan after a successful movie import or movie undo |
| `SCAN_AFTER_AI_SERIES_SORT` | `true` | Trigger scan after a successful AI-assisted episode sort |

Generate an API key in the existing Jellyfin dashboard under API Keys. This
project does not configure libraries or metadata providers. A scan asks
Jellyfin to refresh what its own configuration can already access.

Use only the server origin in `JELLYFIN_SERVER_URL`; do not add an editor page
or dashboard route. If Jellyfin is published on the NAS host, the default
`host.docker.internal` address is correct for this Compose file.

### n8n AI identification

| Variable | Default | Meaning |
|---|---:|---|
| `N8N_AGENT_ENABLED` | `false` | Enables automatic filename identification |
| `N8N_AGENT_URL` | `http://n8n:5678/webhook/media-identify` | Production webhook endpoint |
| `N8N_AGENT_SECRET` | empty | Value sent in `X-Video-Manager-Secret` when configured |
| `N8N_AGENT_TIMEOUT_SECONDS` | `45` | End-to-end webhook request timeout |

The URL must be the Webhook node's **production** URL. This is correct:

```text
http://n8n:5678/webhook/media-identify
```

These are not callable webhook URLs:

```text
http://192.168.40.240:5678/workflow/WORKFLOW_ID
http://192.168.40.240:5678/workflow/WORKFLOW_ID?projectId=PROJECT_ID
```

Those addresses open the n8n editor. A production webhook is registered only
after the workflow is saved/published and active. The test URL works only while
the Webhook node is explicitly listening for a test event.

If Header Auth is enabled in the Webhook node, the same secret value must be
configured there and in `N8N_AGENT_SECRET`. AI provider credentials belong in
n8n Credentials, not in this `.env`.

## Local Telegram Bot API environment

Edit:

```text
jellyfin-compose/telegram-bot-api-compose/.env
```

| Variable | Default | Meaning |
|---|---|---|
| `VIDEO_MANAGER_SOURCE_ROOT` | generated clone path | Build context containing the Linux binary and Dockerfile |
| `TELEGRAM_BOT_API_IMAGE_TAG` | `latest` | Local image tag |
| `TELEGRAM_BOT_API_PORT` | `8081` | NAS loopback port |
| `TELEGRAM_API_ID` | placeholder | Numeric application ID from `my.telegram.org`; required |
| `TELEGRAM_API_HASH` | placeholder | Application hash from `my.telegram.org`; required |
| `PUID` | `0` | UID for API data files |
| `PGID` | `0` | GID for API data files |

`TELEGRAM_API_ID`/`HASH` are application credentials, not the BotFather token.
The bot token is used by the Python bot when it calls the API.

The Compose service starts the committed Linux amd64 binary in `--local` mode.
The binary checksum is verified both by `install-or-update.sh` when possible and
inside the Docker build.

## n8n environment

Edit:

```text
jellyfin-compose/n8n-compose/.env
```

| Variable | Default | Meaning |
|---|---|---|
| `N8N_IMAGE` | `docker.n8n.io/n8nio/n8n:latest` | n8n image; pin the current version for migration |
| `N8N_BIND_ADDRESS` | `0.0.0.0` | Host interface for the editor |
| `N8N_PORT` | `5678` | Published editor port |
| `N8N_HOST` | `192.168.40.240` | NAS address advertised to n8n |
| `N8N_EDITOR_BASE_URL` | `http://192.168.40.240:5678/` | Browser editor base URL |
| `N8N_WEBHOOK_URL` | `http://n8n:5678/` | Internal base used to form production webhooks |
| `N8N_SECURE_COOKIE` | `false` | Allows current HTTP-only LAN deployment |
| `N8N_ENCRYPTION_KEY` | placeholder | Key that encrypts stored credentials; required and permanent |

When migrating an existing n8n instance, keep its exact encryption key. A new
key cannot decrypt credentials written with the old one.

For a brand-new instance, generate a key once:

```bash
openssl rand -hex 32
```

## Windows `config.json`

Run `telegram_jellyfin_bot/install.bat` once. It copies
`config.example.json` to `config.json` only when the latter is missing.

### Required values

| JSON key | Meaning |
|---|---|
| `bot_token` | BotFather token |
| `telegram_api_id` | Numeric application ID from `my.telegram.org` |
| `telegram_api_hash` | Application hash |
| `jellyfin_library_path` | Default series library root |

The Local Bot API host is restricted to `127.0.0.1`/`localhost` in Windows
configuration. This prevents accidentally exposing the unauthenticated local
API port on the LAN.

### Paths and commands

| JSON key | Meaning |
|---|---|
| `telegram_bot_api_exe_path` | Windows API executable, normally `tools\\telegram-bot-api.exe` |
| `jellyfin_movie_library_path` | Optional movie library root |
| `movie_staging_path` | Required separate staging root when movie mode is enabled |
| `data_path` | SQLite data directory |
| `logs_path` | log directory |
| `sorter_command` | Argument list for the series organizer; `{mode}` and `{folder}` are replaced by the bot |
| `movie_sorter_command` | Argument list ending in `movie_organizer.py` |
| `fuzzy_search_command` | Argument list ending in `imdb_tool.py` |

Relative paths are resolved from the `telegram_jellyfin_bot` directory. The
provided example uses sibling directories, so keep the root repository layout
intact or replace every command path with the new absolute path.

The series library, movie library, and movie staging directory must be
different and non-nested. The bot validates configured directories and safe
child paths before use.

### Multiple Windows libraries

The Docker environment constructs four libraries automatically. A Windows JSON
deployment can provide a `media_libraries` array using the same shape:

```json
"media_libraries": [
  {
    "key": "animation_series",
    "name": "Animation Series",
    "media_kind": "series",
    "path": "D:\\Media\\animation-serise"
  },
  {
    "key": "animation_movies",
    "name": "Animation Movies",
    "media_kind": "movie",
    "path": "D:\\Media\\animation-movie"
  },
  {
    "key": "video_series",
    "name": "Video Series",
    "media_kind": "series",
    "path": "D:\\Media\\video-serise"
  },
  {
    "key": "video_movies",
    "name": "Video Movies",
    "media_kind": "movie",
    "path": "D:\\Media\\video-movie"
  }
],
"default_library_key": "animation_series"
```

Each `key` must be unique, `media_kind` must be `series` or `movie`, and the
path must point at the corresponding library root.

### Other Windows keys

The lowercase JSON forms correspond directly to the Docker settings:

- `telegram_download_read_timeout_seconds`
- `sorter_timeout_seconds`
- `movie_sorter_timeout_seconds`
- `scan_after_movie_import`
- `scan_after_ai_series_sort`
- `allowed_chat_ids`
- `allowed_video_extensions`
- `max_parallel_downloads`
- `default_target_folder`
- `confirm_before_download`
- `ask_before_overwrite`
- `jellyfin_server_url`
- `jellyfin_api_key`
- `jellyfin_request_timeout_seconds`
- `jellyfin_scan_poll_interval_seconds`
- `jellyfin_scan_monitor_timeout_seconds`
- `fuzzy_search_timeout_seconds`
- `n8n_agent_enabled`
- `n8n_agent_url`
- `n8n_agent_secret`
- `n8n_agent_timeout_seconds`

## Applying configuration changes

### Docker

Editing `.env` does not change an existing container until it is recreated:

```bash
cd /path/to/jellyfin-compose/video-manager-compose
docker compose config
docker compose up -d
```

No image rebuild is needed for environment-only changes. Rebuild when Python
source, dependencies, or either Dockerfile changes:

```bash
docker compose build --pull
docker compose up -d
```

### Windows

Stop `run.bat`, edit `config.json`, and start `run.bat` again. Restart the Local
Bot API only when its executable, API credentials, host, port, or data directory
changes.

## Configuration validation checklist

Before normal use:

- all placeholder tokens/keys have been replaced or intentionally left empty;
- the four host library paths already exist;
- the bot process can write all four `/media/...` mounts;
- movie staging is writable and outside final libraries;
- the Local Bot API is healthy;
- `N8N_AGENT_URL` uses `/webhook/media-identify`, not `/workflow/...`;
- the n8n workflow is active and returns one JSON identification object;
- Jellyfin API URL/key work if scan commands are enabled;
- `/libraries` displays all intended destinations;
- `/language` and `/menu` persist for the current Telegram chat.
