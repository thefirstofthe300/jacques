"""Unit tests for Job model properties.

These tests exercise pure-Python logic on Job instances directly — no DB or
HTTP layer is needed, so no async fixtures are required.
"""
import json

import pytest

from jacques.models.job import Job, JobStatus


# ── is_active ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status, expected",
    [
        (JobStatus.DETECTED, True),
        (JobStatus.IDENTIFYING, True),
        (JobStatus.RIPPING, True),
        (JobStatus.TRANSCODING, True),
        (JobStatus.FETCHING_METADATA, True),
        (JobStatus.ORGANIZING, True),
        (JobStatus.COMPLETE, False),
        (JobStatus.FAILED, False),
        (JobStatus.AWAITING_SELECTION, False),
    ],
)
def test_is_active(status, expected):
    job = Job(drive_path="/dev/sr0", status=status)
    assert job.is_active is expected


# ── parsed_candidates ─────────────────────────────────────────────────────────


def test_parsed_candidates_none_returns_empty_list():
    job = Job(drive_path="/dev/sr0", candidates=None)
    assert job.parsed_candidates == []


def test_parsed_candidates_empty_string_returns_empty_list():
    """An empty string is falsy; parsed_candidates should return [] not raise."""
    job = Job(drive_path="/dev/sr0", candidates="")
    assert job.parsed_candidates == []


def test_parsed_candidates_deserializes_json():
    payload = [
        {"id": 1, "title": "The Matrix", "year": 1999},
        {"id": 2, "title": "The Matrix Reloaded", "year": 2003},
    ]
    job = Job(drive_path="/dev/sr0", candidates=json.dumps(payload))
    result = job.parsed_candidates
    assert result == payload
    assert isinstance(result, list)
    assert result[0]["title"] == "The Matrix"


def test_parsed_candidates_single_item_list():
    payload = [{"id": 42, "title": "Alien", "year": 1979}]
    job = Job(drive_path="/dev/sr0", candidates=json.dumps(payload))
    assert job.parsed_candidates == payload
