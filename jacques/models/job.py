import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class JobStatus(str, enum.Enum):
    DETECTED = "detected"
    IDENTIFYING = "identifying"
    RIPPING = "ripping"
    TRANSCODING = "transcoding"
    FETCHING_METADATA = "fetching_metadata"
    ORGANIZING = "organizing"
    COMPLETE = "complete"
    FAILED = "failed"
    AWAITING_SELECTION = "awaiting_selection"
    DUPLICATE_DETECTED = "duplicate_detected"
    AWAITING_EPISODE_ASSIGNMENT = "awaiting_episode_assignment"
    AWAITING_TITLE_SELECTION = "awaiting_title_selection"


class DiscType(str, enum.Enum):
    MOVIE = "movie"
    TV_SHOW = "tv_show"
    UNKNOWN = "unknown"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    drive_path: Mapped[str] = mapped_column(String, nullable=False)
    disc_label: Mapped[str | None] = mapped_column(String)
    disc_uuid: Mapped[str | None] = mapped_column(String)
    disc_type: Mapped[DiscType] = mapped_column(
        Enum(DiscType), default=DiscType.UNKNOWN, nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), default=JobStatus.DETECTED, nullable=False
    )
    title: Mapped[str | None] = mapped_column(String)
    year: Mapped[int | None] = mapped_column(Integer)
    tmdb_id: Mapped[int | None] = mapped_column(Integer)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String)
    candidates: Mapped[str | None] = mapped_column(String)
    titles_json: Mapped[str | None] = mapped_column(String)
    episode_assignments: Mapped[str | None] = mapped_column(String)
    selected_title_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    @property
    def display_name(self) -> str:
        if self.title:
            return f"{self.title} ({self.year})" if self.year else self.title
        return self.disc_label or self.drive_path

    @property
    def is_active(self) -> bool:
        return self.status not in (
            JobStatus.COMPLETE,
            JobStatus.FAILED,
            JobStatus.AWAITING_SELECTION,
            JobStatus.DUPLICATE_DETECTED,
            JobStatus.AWAITING_EPISODE_ASSIGNMENT,
            JobStatus.AWAITING_TITLE_SELECTION,
        )

    @property
    def parsed_candidates(self) -> list[dict]:
        if not self.candidates:
            return []
        import json
        return json.loads(self.candidates)

    @property
    def parsed_titles(self) -> list[dict]:
        if not self.titles_json:
            return []
        import json
        return json.loads(self.titles_json)

    @property
    def parsed_episode_assignments(self) -> dict:
        if not self.episode_assignments:
            return {}
        import json
        return json.loads(self.episode_assignments)
