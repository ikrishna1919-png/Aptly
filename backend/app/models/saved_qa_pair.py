"""The extension learning loop: canonical application questions + answers.

Each row is one canonical question the user has answered (e.g. "Do you require
visa sponsorship?") with the variants we've clustered onto it
(`question_examples`) and the saved `answer`. Reused across application sites:
the next time a semantically-equivalent question shows up, we return the saved
answer instead of asking again.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SavedQAPair(Base):
    __tablename__ = "saved_qa_pairs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_canonical: Mapped[str] = mapped_column(Text, nullable=False)
    # Variants we've seen + clustered onto this canonical (list[str]).
    question_examples: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    # text | select | radio | checkbox | date | file
    field_type: Mapped[str] = mapped_column(String(16), nullable=False, default="text")
    source_ats: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    times_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
