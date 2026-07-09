# Feature 04: Metadata

**Status:** Complete

## Summary

Query the TMDb API to identify the movie or TV show, retrieving title, year, and content type. This data drives the final file naming.

## Implementation Plan

- `services/metadata.py` — `MetadataService` class
- Uses `httpx` async client for TMDb API v3
- Searches by disc label, refines with year if available

## TMDb API Calls

### Movie Search
```
GET https://api.themoviedb.org/3/search/movie
  ?api_key=<key>
  &query=<disc_label>
  &year=<year>  (optional)
```

### TV Search
```
GET https://api.themoviedb.org/3/search/tv
  ?api_key=<key>
  &query=<disc_label>
```

## Disc Type Detection

1. Start with heuristic from IDENTIFYING stage (single long title = movie; multiple similar-length titles = TV)
2. Try movie search first; if confidence score is low, try TV search
3. Use whichever result has the highest `popularity` score
4. If no match found: keep `disc_type=unknown`, use disc label as filename

## Data Stored

After this stage, the Job record has:
- `title` — canonical TMDb title
- `year` — release year
- `disc_type` — `movie` or `tv_show`
- `tmdb_id` — TMDb ID for future use

## Configuration

- `JACQUES_TMDB_API_KEY` — required for this stage; if unset, stage is skipped and disc label is used as filename

## Dependencies

- `httpx` (async HTTP client)
- TMDb API key (free at themoviedb.org)
