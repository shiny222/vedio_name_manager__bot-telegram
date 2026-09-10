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
- Existing movie videos are never replaced without the explicit
  `--replace-existing` option.
- An approved replacement archives old video/subtitle media under
  `.replacement_backups/BATCH_ID`; it never deletes the old copy.
- Downloads should be completed in a separate staging folder first.
- Every completed move is written to `.rename_history.json`.
- A durable operation journal is written before each move.
- History is validated before moving a file, including replacement backups.
- Imports search existing folders by IMDb ID, or title and year when an ID is
  missing. Ambiguous matches stop the import; legacy folders are reused rather
  than duplicated. Manual imports can omit the IMDb ID.
- Undo verifies file size and refuses to overwrite the original path.

## Commands

```powershell
python movie_organizer.py dry-run --source "D:\MovieIncoming\movie.mkv" --library "D:\Jellyfin\Movies" --title "Interstellar" --year 2014 --imdb-id tt0816692

python movie_organizer.py import --source "D:\MovieIncoming\movie.mkv" --library "D:\Jellyfin\Movies" --title "Interstellar" --year 2014 --imdb-id tt0816692

python movie_organizer.py import --source "D:\MovieIncoming\new.mkv" --library "D:\Jellyfin\Movies" --title "Interstellar" --year 2014 --imdb-id tt0816692 --replace-existing

python movie_organizer.py undo-last --library "D:\Jellyfin\Movies"

python movie_organizer.py undo-batch BATCH_ID --library "D:\Jellyfin\Movies"

python movie_organizer.py undo-folder "D:\Jellyfin\Movies\Interstellar (2014) [imdbid-tt0816692]"

python movie_organizer.py recover-folder "D:\Jellyfin\Movies\Interstellar (2014) [imdbid-tt0816692]" --staging "D:\MovieIncoming" --source "D:\MovieIncoming\movie.mkv"
```

The Telegram bot invokes these commands through an optional subprocess bridge.
For an existing same-IMDb folder whose spelling differs from the current search
result, the bridge also supplies the trusted `--destination-name` so the tool
replaces that folder's media instead of creating a duplicate identity.

## Rollback details

Each move receives one batch ID. After a verified move, the tool appends a
record to `.rename_history.json` in that movie folder with the original path,
new path, names, size, file type, timestamp, status, and batch ID. The operation
journal records the intent before moving, which makes an interrupted operation
diagnosable.

`recover-folder` checks journaled paths and file sizes, repairs completed move
or undo history, and reports the video's verified location. Ambiguous states
remain unresolved. If a replacement stopped after archiving the old movie but
before importing the new one, recovery restores those backups for a safe retry.
Keep both history and journal files.

In the bot, `/movie_recover ID` synchronizes the queue with that verified
location. After undo, `/movie_forward ID` (or `/movie_import ID`) imports the
restored staging file again. Original staging paths are saved before import;
older jobs recover their staging path from matching history when available.
If neither source is available, recovery stops rather than guessing a filename.

Undo processes the newest records first, verifies the current file size, and
moves each file back to its original staging path. It never replaces a file at
that original path. Successful records become `undone`; conflicts are reported
as skipped and can be retried after the blocking file is removed.

For replacement batches, archive moves are recorded before the incoming import.
Reverse-order undo therefore returns the new file to staging first, then restores
the previous movie and subtitles to their original Jellyfin paths.
