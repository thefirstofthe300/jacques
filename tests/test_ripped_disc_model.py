"""Integration tests for the RippedDisc ORM model.

These tests exercise the DB-level constraints and default values by flushing
objects to an in-memory SQLite session (provided by the shared `db_factory`
fixture in conftest.py).
"""
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from jacques.models.ripped_disc import RippedDisc


# ── helpers ───────────────────────────────────────────────────────────────────


async def _add(session, obj):
    """Add *obj*, flush, and refresh so server-side defaults are visible."""
    session.add(obj)
    await session.flush()
    await session.refresh(obj)
    return obj


# ── disc_label only ───────────────────────────────────────────────────────────


async def test_create_with_label_only_succeeds(db_factory):
    """A RippedDisc with disc_label set and disc_uuid=None is valid."""
    async with db_factory() as session:
        disc = await _add(session, RippedDisc(disc_label="The Matrix"))

    assert disc.id is not None
    assert disc.disc_label == "The Matrix"
    assert disc.disc_uuid is None


async def test_create_with_label_only_populates_ripped_at(db_factory):
    """ripped_at is auto-populated to a datetime value.

    SQLite strips tzinfo on the round-trip, so we assert the column is a
    datetime (not None) and that it's recent rather than checking tzinfo.
    """
    async with db_factory() as session:
        disc = await _add(session, RippedDisc(disc_label="Alien"))

    assert isinstance(disc.ripped_at, datetime)
    # The default is generated from datetime.now(timezone.utc); confirm it's
    # a recent timestamp (within a minute of now).
    from datetime import timezone

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    ripped_naive = disc.ripped_at.replace(tzinfo=None)
    assert abs((now_naive - ripped_naive).total_seconds()) < 60


# ── disc_uuid only ────────────────────────────────────────────────────────────


async def test_create_with_uuid_only_succeeds(db_factory):
    """A RippedDisc with disc_uuid set and disc_label=None is valid."""
    async with db_factory() as session:
        disc = await _add(
            session, RippedDisc(disc_uuid="550e8400-e29b-41d4-a716-446655440000")
        )

    assert disc.id is not None
    assert disc.disc_uuid == "550e8400-e29b-41d4-a716-446655440000"
    assert disc.disc_label is None


# ── both fields set ───────────────────────────────────────────────────────────


async def test_create_with_both_fields_succeeds(db_factory):
    """A RippedDisc with both disc_label and disc_uuid set is valid."""
    async with db_factory() as session:
        disc = await _add(
            session,
            RippedDisc(
                disc_label="Blade Runner",
                disc_uuid="6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            ),
        )

    assert disc.disc_label == "Blade Runner"
    assert disc.disc_uuid == "6ba7b810-9dad-11d1-80b4-00c04fd430c8"


# ── check constraint ──────────────────────────────────────────────────────────


async def test_both_null_raises_integrity_error(db_factory):
    """The ck_ripped_discs_label_or_uuid constraint fires when both are null."""
    async with db_factory() as session:
        with pytest.raises(IntegrityError):
            await _add(session, RippedDisc())


# ── job_id nullable ───────────────────────────────────────────────────────────


async def test_job_id_is_nullable(db_factory):
    """job_id may be omitted (None) without violating any constraint."""
    async with db_factory() as session:
        disc = await _add(session, RippedDisc(disc_label="Interstellar"))

    assert disc.job_id is None


async def test_job_id_can_be_set(db_factory):
    """job_id accepts an integer value (soft foreign-key, no referential check)."""
    async with db_factory() as session:
        disc = await _add(
            session, RippedDisc(disc_label="2001: A Space Odyssey", job_id=42)
        )

    assert disc.job_id == 42
