"""Tests for jacques.api.app module-level helpers (not the FastAPI routes)."""
from jacques.api.app import _status_class


def test_status_class_ripping_awaiting_selection():
    assert _status_class("ripping_awaiting_selection") == "bg-warning text-dark"


def test_status_class_awaiting_selection():
    assert _status_class("awaiting_selection") == "bg-warning text-dark"


def test_status_class_unknown_status_falls_back_to_secondary():
    assert _status_class("some_unrecognized_status") == "bg-secondary"
