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
        (JobStatus.DUPLICATE_DETECTED, False),
        (JobStatus.AWAITING_EPISODE_ASSIGNMENT, False),
        (JobStatus.AWAITING_TITLE_SELECTION, False),
    ],
)
def test_is_active(status, expected):
    job = Job(drive_path="/dev/sr0", status=status)
    assert job.is_active is expected


def test_is_active_false_for_duplicate_detected():
    """DUPLICATE_DETECTED is explicitly excluded from active statuses."""
    job = Job(drive_path="/dev/sr0", status=JobStatus.DUPLICATE_DETECTED)
    assert job.is_active is False


def test_is_active_false_for_awaiting_episode_assignment():
    """AWAITING_EPISODE_ASSIGNMENT is a user-action pause, not an active status."""
    job = Job(drive_path="/dev/sr0", status=JobStatus.AWAITING_EPISODE_ASSIGNMENT)
    assert job.is_active is False


def test_is_active_false_for_awaiting_title_selection():
    """AWAITING_TITLE_SELECTION is a user-action pause, not an active status."""
    job = Job(drive_path="/dev/sr0", status=JobStatus.AWAITING_TITLE_SELECTION)
    assert job.is_active is False


def test_is_active_true_for_ripping():
    """Spot-check that a mid-pipeline status is still considered active."""
    job = Job(drive_path="/dev/sr0", status=JobStatus.RIPPING)
    assert job.is_active is True


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


# ── parsed_titles ─────────────────────────────────────────────────────────────


def test_parsed_titles_none_returns_empty_list():
    job = Job(drive_path="/dev/sr0", titles_json=None)
    assert job.parsed_titles == []


def test_parsed_titles_empty_string_returns_empty_list():
    """An empty string is falsy; parsed_titles should return [] not raise."""
    job = Job(drive_path="/dev/sr0", titles_json="")
    assert job.parsed_titles == []


def test_parsed_titles_deserializes_json():
    payload = [
        {"id": 0, "name": "Title 0", "duration_seconds": 5400, "expected_bytes": None},
        {"id": 1, "name": "Title 1", "duration_seconds": 1200, "expected_bytes": None},
    ]
    job = Job(drive_path="/dev/sr0", titles_json=json.dumps(payload))
    result = job.parsed_titles
    assert result == payload
    assert isinstance(result, list)
    assert result[0]["name"] == "Title 0"


# ── parsed_episode_assignments ─────────────────────────────────────────────────


def test_parsed_episode_assignments_none_returns_empty_dict():
    job = Job(drive_path="/dev/sr0", episode_assignments=None)
    assert job.parsed_episode_assignments == {}


def test_parsed_episode_assignments_empty_string_returns_empty_dict():
    """An empty string is falsy; parsed_episode_assignments should return {} not raise."""
    job = Job(drive_path="/dev/sr0", episode_assignments="")
    assert job.parsed_episode_assignments == {}


def test_parsed_episode_assignments_deserializes_json():
    payload = {"0": 1, "1": 2, "2": 3}
    job = Job(drive_path="/dev/sr0", episode_assignments=json.dumps(payload))
    result = job.parsed_episode_assignments
    assert result == payload
    assert isinstance(result, dict)


# ── disc_uuid field ───────────────────────────────────────────────────────────


def test_disc_uuid_can_be_set():
    """Job accepts a disc_uuid string on construction."""
    uuid = "550e8400-e29b-41d4-a716-446655440000"
    job = Job(drive_path="/dev/sr0", disc_uuid=uuid)
    assert job.disc_uuid == uuid


def test_disc_uuid_defaults_to_none():
    """disc_uuid is nullable; omitting it leaves it as None (backward compat)."""
    job = Job(drive_path="/dev/sr0")
    assert job.disc_uuid is None


# ── JobStatus enum serialization ──────────────────────────────────────────────


def test_duplicate_detected_serializes_to_string():
    """DUPLICATE_DETECTED's string value must be the literal 'duplicate_detected'.

    JobStatus inherits from str, so equality comparison and .value are the
    serialization paths used by SQLAlchemy and JSON responses.
    """
    assert JobStatus.DUPLICATE_DETECTED == "duplicate_detected"
    assert JobStatus.DUPLICATE_DETECTED.value == "duplicate_detected"
