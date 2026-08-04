# Jellyfin Movie Organizer

This independent tool safely imports one confirmed movie into a Jellyfin Movies
library. It does not contact IMDb and it does not require the Telegram bot.

The caller supplies the confirmed title, optional year, and optional IMDb ID.
The tool creates one Jellyfin folder per movie and gives the video and matching
subtitles the same base name.

```text
Movies/
`-- Interstellar (2014) [imdbid-tt0816692]/
    |-- Interstellar (2014) [imdbid-tt0816692].mkv
    |-- Interstellar (2014) [imdbid-tt0816692].fa.srt
    `-- .rename_history.json
```

## Safety

- The source must be outside the final Movies library.
- Existing movie videos and destination files are never overwritten.
- Downloads should be completed in a separate staging folder first.
- Every completed move is written to `.rename_history.json`.
- A durable operation journal is written before each move.
- Undo verifies file size and refuses to overwrite the original path.

## Commands

```powershell
python movie_organizer.py dry-run --source "D:\MovieIncoming\movie.mkv" --library "D:\Jellyfin\Movies" --title "Interstellar" --year 2014 --imdb-id tt0816692

python movie_organizer.py import --source "D:\MovieIncoming\movie.mkv" --library "D:\Jellyfin\Movies" --title "Interstellar" --year 2014 --imdb-id tt0816692

python movie_organizer.py undo-last --library "D:\Jellyfin\Movies"

python movie_organizer.py undo-batch BATCH_ID --library "D:\Jellyfin\Movies"

python movie_organizer.py undo-folder "D:\Jellyfin\Movies\Interstellar (2014) [imdbid-tt0816692]"
```

The Telegram bot invokes these commands through an optional subprocess bridge.

## Rollback details

Each move receives one batch ID. After a verified move, the tool appends a
record to `.rename_history.json` in that movie folder with the original path,
new path, names, size, file type, timestamp, status, and batch ID. The operation
journal records the intent before moving, which makes an interrupted operation
diagnosable.

Undo processes the newest records first, verifies the current file size, and
moves each file back to its original staging path. It never replaces a file at
that original path. Successful records become `undone`; conflicts are reported
as skipped and can be retried after the blocking file is removed.
