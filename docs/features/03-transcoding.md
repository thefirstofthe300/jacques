# Feature 03: Transcoding

**Status:** Complete

## Summary

Convert ripped MKV files to H.265/HEVC using HandBrakeCLI with RF 20 quality. Preserve all audio tracks and subtitles.

## Implementation Plan

- `services/transcoder.py` — `Transcoder` class
- Uses `asyncio.create_subprocess_exec` for non-blocking HandBrakeCLI execution
- Streams stdout line-by-line to parse progress percentage

## HandBrake Command

```bash
HandBrakeCLI \
  -i /tmp/jacques/<job_id>/raw/input.mkv \
  -o /tmp/jacques/<job_id>/transcoded/output.mkv \
  --encoder x265 \
  --quality 20 \
  --encoder-preset medium \
  --audio-lang-list und \
  --all-audio \
  --aencoder copy \
  --subtitle-lang-list und \
  --all-subtitles
```

## Quality Settings

| Setting | Value | Notes |
|---|---|---|
| Encoder | x265 | H.265/HEVC |
| Quality (RF) | 20 | Range 18–22; lower = better quality |
| Preset | medium | Balance of speed and compression |
| Audio | copy | Preserve original audio tracks |
| Subtitles | all | Preserve all subtitle tracks |

## Progress Parsing

HandBrakeCLI writes progress as:
```
Encoding: task 1 of 1, 45.23 % (125.34 fps, avg 130.12 fps, ETA 00h12m34s)
```

Parse the percentage to update `job.progress`.

## Hardware Acceleration (Future)

Consider adding `--enable-hw-decoding` and NVENC/QSV encoding for GPU acceleration. Not in initial implementation to keep dependencies minimal.

## Dependencies

- `HandBrakeCLI` binary installed on host
