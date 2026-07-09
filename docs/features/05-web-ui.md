# Feature 05: Web UI

**Status:** Complete (initial dashboard)

## Summary

FastAPI-powered web interface showing live job status with HTMX-driven auto-refresh. No authentication — designed for local network use.

## Routes

| Method | Path | Description |
|---|---|---|
| GET | `/` | Job dashboard (full page) |
| GET | `/partials/jobs` | Job list partial for HTMX polling |
| GET | `/api/jobs` | Job list as JSON |
| GET | `/api/jobs/{id}` | Single job as JSON |

## Dashboard Features

- Job list with status badges, disc label, drive path
- Progress bar for active jobs (ripping/transcoding)
- Error message display for failed jobs
- Auto-refresh every 3 seconds via HTMX polling
- Empty state when no jobs exist

## Status Badge Colors

| Status | Color |
|---|---|
| detected | gray (secondary) |
| identifying | blue (info) |
| ripping | blue (primary) |
| transcoding | blue (primary) |
| fetching_metadata | yellow (warning) |
| organizing | yellow (warning) |
| complete | green (success) |
| failed | red (danger) |

## Tech Stack

- FastAPI + Jinja2 templates
- Bootstrap 5.3 (dark mode, CDN)
- Bootstrap Icons (CDN)
- HTMX 1.9 (CDN) — polling for live updates

## Future Additions

- Manual job retry button for failed jobs
- Disc eject button
- Job detail page (title list, transcode settings)
- Configuration UI (output path, quality, TMDb key)
- Drive status panel showing currently inserted discs
