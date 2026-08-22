FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="Jellyfin Video Manager Bot"
LABEL org.opencontainers.image.description="Telegram download bot with independent series, movie, and IMDb tools"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIDEO_MANAGER_CONFIG_MODE=env

WORKDIR /app

COPY telegram_jellyfin_bot/requirements.txt /tmp/requirements-bot.txt
COPY organizer/requirements.txt /tmp/requirements-organizer.txt
COPY fuzzy_search/requirements.txt /tmp/requirements-fuzzy-search.txt
COPY movie_organizer/requirements.txt /tmp/requirements-movie-organizer.txt

RUN python -m pip install --upgrade pip \
    && python -m pip install \
        -r /tmp/requirements-bot.txt \
        -r /tmp/requirements-organizer.txt \
        -r /tmp/requirements-fuzzy-search.txt \
        -r /tmp/requirements-movie-organizer.txt \
    && groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/app app

COPY --chown=1000:1000 telegram_jellyfin_bot /app/telegram_jellyfin_bot
COPY --chown=1000:1000 organizer /app/organizer
COPY --chown=1000:1000 movie_organizer /app/movie_organizer
COPY --chown=1000:1000 fuzzy_search /app/fuzzy_search

RUN mkdir -p /app/data /app/logs /app/staging /app/fuzzy_search/data \
    && chown -R 1000:1000 /app/data /app/logs /app/staging /app/fuzzy_search/data

USER 1000:1000

CMD ["python", "-m", "telegram_jellyfin_bot.bot"]
