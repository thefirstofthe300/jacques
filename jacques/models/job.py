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
    RIPPING_AWAITING_SELECTION = "ripping_awaiting_selection"


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

    def to_response_dict(self) -> dict:
        """Serialize this job into the API/SSE response shape.

        Single source of truth for job->dict serialization, shared by the API
        route layer (`JobResponse.from_job`) and the daemon's SSE broadcasts —
        keeps the daemon from depending on the route layer for presentation
        logic.
        """
        return {
            "id": self.id,
            "drive_path": self.drive_path,
            "disc_label": self.disc_label,
            "disc_uuid": self.disc_uuid,
            "disc_type": self.disc_type.value,
            "status": self.status.value,
            "title": self.title,
            "year": self.year,
            "progress": self.progress,
            "error_message": self.error_message,
            "display_name": self.display_name,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "candidates": self.parsed_candidates,
            "titles": self.parsed_titles,
            "episode_assignments": self.parsed_episode_assignments,
            "selected_title_id": self.selected_title_id,
        }
