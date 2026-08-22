# NAS Docker deployment

This deployment keeps Jellyfin, Video Manager, Local Telegram Bot API, and n8n
as separate Compose projects under the existing `jellyfin-compose` directory.
The Python and Telegram API images are built locally on the NAS from this Git
repository. n8n and Jellyfin continue using their upstream images.

## Resulting NAS layout

```text
jellyfin-compose/
|-- docker-compose.yml                 existing Jellyfin Compose file
|-- config/                            existing Jellyfin configuration
|-- cache/                             existing Jellyfin cache
|-- source/                            this Git repository
|-- video-manager-compose/
|   |-- docker-compose.yml
|   |-- .env
|   |-- data/
|   |-- logs/
|   `-- staging/
|-- telegram-bot-api-compose/
|   |-- docker-compose.yml
|   |-- .env
|   `-- data/
`-- n8n-compose/
    |-- docker-compose.yml
    |-- .env
    `-- data/
```

Runtime `.env` files and persistent directories are outside the Git source
clone. A later `git pull` cannot replace them.

## 1. Clone and create the layout

Run as root on the NAS:

```bash
cd /srv/dev-disk-by-uuid-e5048a2d-8521-41d1-8efc-880e999ecc6f/Archive/jellyfin-compose

git clone https://github.com/shiny222/vedio_name_manager__bot-telegram.git source

bash source/nas/install-or-update.sh "$PWD"
```

The setup script performs only deployment preparation:

- copies the three tracked Compose templates into sibling directories;
- creates `.env` from `.env.example` only when `.env` does not exist;
- creates persistent data/log/staging directories;
- creates the external `media-automation` network for the three automation
  containers when missing.

It does not read, modify, recreate, stop, or restart the existing Jellyfin
Compose project.

## 2. Configure secrets

Edit the Local Bot API settings:

```bash
nano telegram-bot-api-compose/.env
```

Set the numeric Telegram API ID and API hash from `my.telegram.org`.

Edit the Video Manager settings:

```bash
nano video-manager-compose/.env
```

At minimum, set:

```env
BOT_TOKEN=your_bot_token
JELLYFIN_API_KEY=your_jellyfin_api_key
```

Leave `JELLYFIN_API_KEY` empty if scan/status commands are not needed yet. The
download and organizer functions still work.

All four NAS libraries are exposed to the bot at the same time:

```env
LIBRARY_ANIMATION_SERIES_PATH=/media/animation-serise
LIBRARY_ANIMATION_MOVIE_PATH=/media/animation-movie
LIBRARY_VIDEO_SERIES_PATH=/media/video-serise
LIBRARY_VIDEO_MOVIE_PATH=/media/video-movie
DEFAULT_LIBRARY_KEY=animation_series
```

After the bot starts, send `/libraries` and choose Animation Series, Animation
Movies, Video Series, or Video Movies. The choice is stored separately for each
chat and automatically changes that chat to series or movie mode. Every queued
file remembers the chosen library, so selecting another destination later does
not reroute it.

## 3. Stop the old Windows processes

Stop the Windows bot and Windows Local Telegram Bot API before starting the NAS
copies. Do not run two Local Bot API servers or two polling bot processes for
the same bot token at the same time.

## 4. Leave Jellyfin unchanged

Confirm that the already-installed Jellyfin server is still available:

```bash
curl http://127.0.0.1:8096/System/Info/Public
```

Do not run any new Jellyfin installation or Compose command for this deployment.
The existing server already publishes port `8096`. Video Manager reaches that
port through Docker's `host.docker.internal` host-gateway mapping. Jellyfin does
not join the automation network and its existing Compose file, container,
configuration, cache, libraries, plugins, and metadata remain untouched.

## 5. Build and start Local Telegram Bot API

```bash
cd /srv/dev-disk-by-uuid-e5048a2d-8521-41d1-8efc-880e999ecc6f/Archive/jellyfin-compose/telegram-bot-api-compose

docker compose config
docker compose build --pull
docker compose up -d
docker compose logs -f
```

Press `Ctrl+C` to leave the log view; the container continues running.

The included Linux binary is amd64 and requires glibc 2.38 and OpenSSL 3. Its
Dockerfile uses Ubuntu 24.04 to provide compatible runtime libraries. The API
port is published only on NAS loopback and is also available to containers as
`http://telegram-bot-api:8081` on the shared network.

Both the setup script and Docker build verify the committed SHA-256 checksum of
the Linux binary before it is installed in the image.

## 6. Build and start Video Manager

```bash
cd /srv/dev-disk-by-uuid-e5048a2d-8521-41d1-8efc-880e999ecc6f/Archive/jellyfin-compose/video-manager-compose

docker compose config
docker compose build --pull
docker compose up -d
docker compose logs -f
```

The bot reads its configuration from `.env`; it does not need a Docker
`config.json`. Existing Windows `config.json` behavior remains available when
the project is run outside Docker.

The Local Bot API data directory is mounted read-only at the identical container
path in Video Manager. This allows the downloader to copy Telegram's completed
local files without exposing that directory to the organizer for writing.

### Moving existing Windows state

Starting with empty `data` directories is the safest first test. If the old bot
state must be retained, stop the Windows bot first and copy `state.db` together
with any `state.db-wal` and `state.db-shm` files into
`video-manager-compose/data`. Do not copy a live SQLite database.

The old Local Bot API and new Local Bot API must not run together. Telegram's
official migration procedure recommends closing the old local server and moving
its bot subdirectory when uninterrupted update continuity is required.

Organizer histories created by the new container store stable `/media/...`
paths and remain usable after image upgrades. Older history records containing
Windows drive-letter paths cannot be safely undone in Linux until a deliberate
path-migration tool is run. Keep those JSON files, but do not attempt an old
Windows batch undo from the container.

## 7. Start or migrate n8n

For a brand-new n8n installation, edit:

```bash
nano /srv/dev-disk-by-uuid-e5048a2d-8521-41d1-8efc-880e999ecc6f/Archive/jellyfin-compose/n8n-compose/.env
```

Generate an encryption key once:

```bash
openssl rand -hex 32
```

Put the result in `N8N_ENCRYPTION_KEY`, then start n8n:

```bash
cd /srv/dev-disk-by-uuid-e5048a2d-8521-41d1-8efc-880e999ecc6f/Archive/jellyfin-compose/n8n-compose
docker compose pull
docker compose up -d
docker compose logs -f
```

For an existing n8n installation, do not start the new instance until its data
and original `N8N_ENCRYPTION_KEY` have been copied. Losing that key makes stored
credentials unreadable. Pin `N8N_IMAGE` to the existing n8n version for the
first migration rather than changing versions at the same time.

n8n is deployed now but is not yet connected to Telegram. The current bot keeps
using polling. A later phase will switch exactly once from polling to an n8n
webhook; both receivers must not be active together.

## Status and troubleshooting

Show every stack:

```bash
cd /srv/dev-disk-by-uuid-e5048a2d-8521-41d1-8efc-880e999ecc6f/Archive/jellyfin-compose
docker compose ps

cd telegram-bot-api-compose && docker compose ps && cd ..
cd video-manager-compose && docker compose ps && cd ..
cd n8n-compose && docker compose ps && cd ..
```

Check the shared network:

```bash
docker network inspect media-automation
```

If a media mount is misspelled or missing, Compose refuses to start instead of
silently creating an empty host folder. This is deliberate protection against
writing downloads into the wrong location.

## Updating later

```bash
cd /srv/dev-disk-by-uuid-e5048a2d-8521-41d1-8efc-880e999ecc6f/Archive/jellyfin-compose

git -C source pull --ff-only
bash source/nas/install-or-update.sh "$PWD"

cd telegram-bot-api-compose
docker compose build --pull
docker compose up -d

cd ../video-manager-compose
docker compose build --pull
docker compose up -d
```

The synchronization command refreshes tracked Compose templates but never
overwrites an existing `.env` or persistent runtime directory.
