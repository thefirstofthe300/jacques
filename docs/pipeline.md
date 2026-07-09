# Pipeline State Machine

Each ripping job moves through a linear sequence of states. Any state can transition to `FAILED`.

## States

```
DETECTED
    │
    ▼
IDENTIFYING  ←── MakeMKV reads disc info, selects main title
    │
    ▼
RIPPING  ←── MakeMKV extracts MKV to temp directory
    │
    ▼
TRANSCODING  ←── HandBrakeCLI converts to H.265/HEVC RF 20
    │
    ▼
FETCHING_METADATA  ←── TMDb lookup: title, year, disc type
    │
    ▼
ORGANIZING  ←── Rename + move to final library path
    │
    ▼
COMPLETE

(any state) ──► FAILED
```

## State Details

### DETECTED
Triggered by udev `change` event on a block device with `ID_TYPE=cd` and `ID_CDROM_MEDIA=1`.

Data captured: `drive_path` (e.g. `/dev/sr0`), `disc_label` (from `ID_FS_LABEL`).

### IDENTIFYING
Calls `makemkvcon -r info disc:N` to enumerate titles.

Logic:
1. Parse all titles with duration
2. Skip titles under 20 minutes (extras/trailers)
3. Prefer the title flagged `(FPL_MainFeature)` by MakeMKV
4. Fallback: select the longest title

Disc type heuristic (refined in `FETCHING_METADATA`):
- Single main title → likely Movie
- Multiple titles of similar length → likely TV Show (multiple episodes)

### RIPPING
Calls `makemkvcon mkv disc:N all /tmp/jacques/<job_id>/` to extract selected titles as MKV files.

Progress is parsed from stdout line-by-line (`PRGV:` lines).

### TRANSCODING
Calls `HandBrakeCLI` with H.265 preset:
```
HandBrakeCLI -i input.mkv -o output.mkv \
  --encoder x265 --quality 20 \
  --audio-lang-list und --all-audio \
  --subtitle-lang-list und --all-subtitles
```

Progress parsed from stdout percentage.

### FETCHING_METADATA
Queries TMDb API:
1. If disc type heuristic is Movie: search `/search/movie?query=<label>&year=<year>`
2. If TV Show: search `/search/tv?query=<label>`
3. Use first result; store `title`, `year`, `tmdb_id`, `disc_type`

### ORGANIZING
Applies Plex/Jellyfin naming:
- Movie: `{output_path}/Movies/{title} ({year})/{title} ({year}).mkv`
- TV: `{output_path}/TV Shows/{title} ({year})/Season {season:02d}/{title} - S{season:02d}E{episode:02d}.mkv`

Moves files from temp directory to final destination, then ejects the disc.

### COMPLETE / FAILED
Terminal states. FAILED includes an `error_message` with context.

## Concurrency Model

Each job is processed sequentially through its stages (detect → identify → rip → transcode → metadata → organize). Multiple jobs from different drives run concurrently via asyncio tasks.

The ripping and transcoding stages are I/O-bound subprocesses. They use `asyncio.create_subprocess_exec` to avoid blocking the event loop, with stdout streamed line-by-line for progress parsing.
