# Jacques — Architecture

Jacques is an automatic disc-ripping daemon with a web UI. It watches optical drives for disc insertion, rips to MKV with MakeMKV, transcodes to H.265 with HandBrake, identifies content via TMDb, and organizes files into Plex/Jellyfin-compatible directory structures.

## System Overview

```
                    ┌────────────────────────────────────────────┐
                    │                  Jacques                    │
                    │                                            │
  Optical Drive ──► │  DiscDetector ──► JobQueue ──► Pipeline   │ ──► Media Library
                    │                                    │        │
                    │              FastAPI Web UI ◄──────┘        │
                    └────────────────────────────────────────────┘
```

## Components

### DiscDetector (`services/detector.py`)
Monitors Linux udev events for optical drive state changes. When a disc with media is detected, it enqueues a new job. Supports multiple concurrent drives.

### Job Queue (`daemon.py`)
An `asyncio.Queue` that decouples detection from processing. Each queue item is a `(drive_path, disc_label)` tuple. The job processor creates the database record and advances the job through pipeline stages.

### Pipeline Stages
See [pipeline.md](pipeline.md) for the complete state machine.

1. **DETECTED** — Disc inserted, job record created
2. **IDENTIFYING** — MakeMKV reads disc info, selects main title
3. **RIPPING** — MakeMKV extracts MKV to temp directory
4. **TRANSCODING** — HandBrakeCLI converts to H.265/HEVC
5. **FETCHING_METADATA** — TMDb lookup for title, year, type
6. **ORGANIZING** — Rename and move to final library path
7. **COMPLETE** — Done
8. **FAILED** — Terminal error with message

### Web UI (`api/`)
FastAPI app with Jinja2 templates and HTMX for real-time job status updates. No authentication — designed for local network use only.

### Database (`database.py`)
SQLite via SQLAlchemy async. Single-file database at a configurable path. Schema is created on startup via `metadata.create_all`.

## Configuration

All settings are in `config.py` (Pydantic Settings). Configurable via environment variables with `JACQUES_` prefix or a `.env` file.

Key settings:
| Setting | Default | Description |
|---|---|---|
| `JACQUES_OUTPUT_PATH` | `/media/library` | Final media destination |
| `JACQUES_TEMP_PATH` | `/tmp/jacques` | Working directory for rip/transcode |
| `JACQUES_HANDBRAKE_QUALITY` | `20` | RF quality (18–22 range; lower = better) |
| `JACQUES_TMDB_API_KEY` | `""` | TMDb v3 API key |
| `JACQUES_HOST` | `0.0.0.0` | Web UI bind address |
| `JACQUES_PORT` | `8080` | Web UI port |

## Technology Stack

| Concern | Library |
|---|---|
| Disc detection | pyudev |
| MKV extraction | python-makemkv (wraps `makemkvcon`) |
| Transcoding | HandBrakeCLI (subprocess) |
| Metadata | httpx + TMDb API v3 |
| Web framework | FastAPI + Uvicorn |
| Templates | Jinja2 + HTMX |
| Database | SQLAlchemy async + aiosqlite |
| Config | pydantic-settings |

## File Naming Conventions

Follows Plex/Jellyfin naming standards:

- **Movies:** `Movies/Title (YYYY)/Title (YYYY).mkv`
- **TV Shows:** `TV Shows/Series Name (YYYY)/Season 01/Series Name - S01E01.mkv`

## Multi-Drive Support

Each optical drive operates independently. Multiple drives can be ripping, transcoding, or organizing simultaneously. The asyncio job processor handles concurrent jobs without blocking.

## Dependency: External Binaries

The following must be installed on the host:
- `makemkvcon` — MakeMKV CLI (from MakeMKV installation)
- `HandBrakeCLI` — HandBrake command line interface
