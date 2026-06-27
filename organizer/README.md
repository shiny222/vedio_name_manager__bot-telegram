# Jellyfin TV Series Organizer

A safe Windows-friendly command-line tool that moves one selected TV series into
Jellyfin's recommended series/season layout. The selected folder's name is always
the trusted series title; filenames are used only for season/episode detection.

The Telegram bot is a separate sibling project and is not required to use this
organizer.

## Requirements

- Python 3.9 or newer
- Windows (the code is portable, but Windows paths are a primary use case)

The easiest setup is to double-click `install.bat`. It creates an isolated
`.venv` and installs the organizer dependency.

Install the optional high-quality filename detector:

```powershell
py -m pip install -r requirements.txt
```

The built-in regex detector still works if `guessit` is unavailable.

## Download folder layout

Put the downloaded files for one series directly inside a folder named after it:

```text
D:\Downloads\Breaking Bad\
|-- random_file_01.mkv
|-- Breaking.Bad.S01E02.1080p.mkv
`-- BB episode 3.mp4
```

Select that exact `Breaking Bad` folder—not `D:\Downloads` and not your whole
Jellyfin library. Only files directly inside the selected folder are processed.
Sibling folders are never scanned. Supported videos are
`.mkv`, `.mp4`, `.avi`, `.mov`, `.webm`, and `.m4v`. Matching `.srt`, `.ass`, and
`.vtt` subtitles (same filename stem) follow their video.

## Usage

### Double-click launcher

On Windows, double-click `start_organizer.bat` and choose an option from the
menu. It asks for only one path: the existing Jellyfin anime/series folder that
contains the loose downloads, such as `D:\JellyfinLibrary\Breaking Bad`.
Season folders are created inside it. Use **Dry run** first.

Preview first—this makes no filesystem changes and writes no history:

```powershell
python organizer.py dry-run --series-folder "D:\JellyfinLibrary\Breaking Bad"
```

Run the organization:

```powershell
python organizer.py run --series-folder "D:\JellyfinLibrary\Breaking Bad"
```

The output becomes:

```text
D:\JellyfinLibrary\Breaking Bad\Season 01\
├── Breaking Bad - S01E01.mkv
├── Breaking Bad - S01E02.mkv
└── Breaking Bad - S01E03.mp4
```

Files without a reliable episode number go to `Series Name\_Unsorted`. If an
intended destination already exists, the incoming file goes to
`Series Name\_Conflicts`; existing files are never overwritten.

## Undo and rollback

Every real move is recorded in `.rename_history.json`. Season moves are recorded
inside their `Season NN` folder. Unsorted and conflict moves are recorded in the
series folder. Each entry stores both full paths, both names, size, type, status,
UTC timestamp, and batch ID.

Undo the latest batch across the library:

```powershell
python organizer.py undo-last --library "D:\JellyfinLibrary"
```

Undo a batch shown in the run output:

```powershell
python organizer.py undo-batch "20260627-120000-a1b2c3d4" --library "D:\JellyfinLibrary"
```

Undo all active records from one history file:

```powershell
python organizer.py undo-folder "D:\JellyfinLibrary\Breaking Bad\Season 01"
```

Before restoring, undo verifies that the organized file exists, that the original
path is free, and that the file size still matches. Unsafe restores are skipped.
Successful entries are marked `undone` and retain their audit history.

## Episode detection

Explicit forms such as `S01E02`, `s1e2`, `1x02`, `Episode 02`, `Ep 02`, `E02`,
`قسمت 2`, and Arabic `حلقة 2` are supported. `guessit` is then used when
available. Finally, a single isolated number can be accepted (for example
`video_001`), while common resolutions, codecs, years, or ambiguous multiple
numbers are rejected.

## Safety notes

- Start with `dry-run`.
- No destination is overwritten during organize or undo.
- History is written atomically after each move.
- If recording a move fails, the program immediately attempts to move that file
  back to its source.
- Keep history files until you no longer need rollback.

The code is split into detection, moving/history, organization, and undo functions
so a future Jellyfin API refresh hook can be added after a successful batch.
