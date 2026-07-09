# Feature 02: Ripping

**Status:** Complete

## Summary

Use `python-makemkv` (wraps `makemkvcon`) to identify the main title on the disc and extract it to MKV in a temp directory.

## Implementation Plan

- `services/ripper.py` — `Ripper` class
- Phase 1: `IDENTIFYING` — call `makemkvcon -r info disc:N`, parse title list, select main title
- Phase 2: `RIPPING` — call `makemkvcon mkv disc:N <title_id> <output_dir>`, stream progress

## Title Selection Logic

1. Parse all titles from `makemkvcon info` output
2. Filter titles shorter than 20 minutes (extras/trailers/menus)
3. Prefer any title flagged `(FPL_MainFeature)` in the name (Blu-ray playlist protection)
4. Fallback: select longest remaining title
5. For TV episodes: select all titles over 20 minutes

## Progress Parsing

MakeMKV writes progress in the format:
```
PRGV:current,total,max
```

Parse these lines to update `job.progress` (0–100).

## Dependencies

- `makemkv` PyPI package (python-makemkv by d-k-bo)
- `makemkvcon` binary installed on host

## Temp Directory Layout

```
/tmp/jacques/<job_id>/
  raw/        ← MakeMKV output (.mkv files)
  transcoded/ ← HandBrake output
```
