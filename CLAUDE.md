# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Set up environment (uv-managed venv; also available via `nix develop`)
uv sync --group dev

# Run full test suite
uv run pytest

# Run a single test file / test
uv run pytest tests/test_pipeline.py -v
uv run pytest tests/test_pipeline.py::test_name -v

# Run the daemon locally
uv run jacques

# Build/run via Nix (wraps in makemkvcon/HandBrakeCLI on PATH)
nix build
nix run
```

No linter or formatter is configured. `doCheck = false` in `flake.nix` — the Nix package build does not run tests (some require the dev shell's `LD_LIBRARY_PATH` fix for greenlet/libstdc++); always validate with `uv run pytest` before committing.

Subprocess calls (`makemkvcon`, `HandBrakeCLI`) and HTTP calls (TMDb) are always mocked in tests (`unittest.mock`/`AsyncMock`, `respx`) — no test depends on real hardware or network.

## Architecture

Jacques is an async daemon (`jacques/daemon.py`) that watches optical drives via udev, rips discs with MakeMKV, transcodes to H.265 with HandBrakeCLI, identifies content via TMDb, and organizes output into Plex/Jellyfin directory layouts. A FastAPI + Jinja2 + HTMX web UI runs in the same process for job monitoring and pause-point resolution.

### Job pipeline and pause points

`_run_pipeline` in `daemon.py` is the core state machine, driving a `Job` row through `JobStatus` values: `DETECTED → IDENTIFYING → RIPPING → TRANSCODING → FETCHING_METADATA... → ORGANIZING → COMPLETE` (or `FAILED` from any stage). See `docs/pipeline.md` for the stage-by-stage state machine and `docs/architecture.md` for the system overview.

The pipeline is re-entrant via a `start_stage` parameter and `_should_run`/`_RERUN_STAGES` — reruns (triggered by the `/api/jobs/{id}/rerun/{stage}` endpoint) resume from a specific stage rather than from scratch, using `.done` marker files under `settings.temp_path/<job_id>/{raw,transcoded}/` to detect already-completed stage output. A separate `rerun_queue` (in `app.state`) decouples API-triggered resumes from the initial detection `job_queue`; both are drained by long-running tasks in `run()`.

Several statuses represent a **pause awaiting user input**, not a failure, and survive daemon restarts (`_reset_interrupted_jobs` preserves them explicitly rather than marking them `FAILED`):
- `AWAITING_SELECTION` — TMDb search returned multiple candidates; user picks one via `/select/{tmdb_id}`.
- `AWAITING_EPISODE_ASSIGNMENT` — multi-title TV disc ripped; user maps each ripped title to season/episode via `/assign-episodes`.
- `AWAITING_TITLE_SELECTION` — ambiguous multi-title movie disc ripped (no clear main feature); user picks which title to keep via `/keep-title/{title_id}`. Discarded titles' raw output is deleted from disk, and the pipeline *also* re-filters `raw_paths` by `selected_title_id` before transcode — cleanup failing on disk must never let a stale title reach the library.
- `DUPLICATE_DETECTED` — disc already recorded in `ripped_discs` (matched by `disc_uuid` first, then `disc_label`); resumable via `/rerip`.

`_titles_to_rip` decides whether to rip one title (clear movie main feature) or all titles (TV show, or a movie with no unambiguous main feature — ripping everything defers the decision to the user rather than guessing wrong).

### Source map

```
jacques/
  config.py          — Settings singleton (pydantic-settings); JACQUES_ env prefix, then ~/.config/jacques/config.toml
  daemon.py           — udev detection loop, job/rerun asyncio queues, _run_pipeline state machine
  database.py         — SQLAlchemy async engine + session factory
  models/
    job.py            — Job (central record), JobStatus, DiscType enums
    ripped_disc.py     — RippedDisc, dedup ledger keyed by disc_uuid/disc_label
  services/
    detector.py        — pyudev disc detection
    ripper.py           — makemkvcon wrapper (TitleInfo, Ripper) — main-feature/TV heuristics
    transcoder.py       — HandBrakeCLI wrapper, progress parsed from --json stdout
    metadata.py         — TMDb client; MediaInfo or list[MediaInfo] when ambiguous
    organizer.py        — Plex/Jellyfin destination naming + move; _safe_name sanitizes filesystem-unsafe chars
  api/
    app.py              — FastAPI app, dashboard route, Jinja2 templates, status→CSS-class filter
    routes/jobs.py       — REST: list/get/delete, rerun, select, assign-episodes, keep-title, rerip
    routes/partials.py   — HTMX partial renders for live job cards
  templates/            — Jinja2 + HTMX, Bootstrap classes, full innerHTML replacement on refresh
tests/                  — pytest, one file per service/concern; in-memory sqlite via conftest.py db_factory fixture
```

### Conventions worth knowing

- All I/O is async (`asyncio.subprocess`, SQLAlchemy async, httpx async); no sync blocking calls except `Organizer.move`, which explicitly runs `shutil.move` via `asyncio.to_thread`.
- `Job.candidates`, `titles_json`, and `episode_assignments` are JSON-serialized text columns (SQLite has no native JSON in this schema) — always go through the `parsed_*` properties on `Job`, never `json.loads` the raw column elsewhere.
- Web UI has no authentication by design — local network use only (also called out in the NixOS module's `openFirewall` option docs).

## Serena

This project is Serena-onboarded (`.serena/` exists). Always use Serena's symbolic tools (`find_symbol`, `find_referencing_symbols`, `replace_symbol_body`, etc.) instead of plain-text Read/Grep/Edit when navigating or editing code here — call `initial_instructions` first if starting a fresh session.

Consult Serena's project memories (`mem:core` as the entry point, which links to `mem:tech_stack`, `mem:conventions`, `mem:suggested_commands`, `mem:task_completion`) for architectural patterns, past design decisions, and established code style before assuming conventions from scratch. Cross-check anything memory-derived against current code — memories can lag behind recent commits.
