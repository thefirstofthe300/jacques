from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RippedDisc(Base):
    __tablename__ = "ripped_discs"
    __table_args__ = (
        CheckConstraint(
            "disc_label IS NOT NULL OR disc_uuid IS NOT NULL",
            name="ck_ripped_discs_label_or_uuid",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    disc_label: Mapped[str | None] = mapped_column(String)
    disc_uuid: Mapped[str | None] = mapped_column(String)
    ripped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    job_id: Mapped[int | None] = mapped_column(Integer)
