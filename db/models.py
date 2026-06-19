from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ModNote(Base):
    """A moderator note recorded against a Discord user.

    Append-only by design: notes are never edited, so the (author, timestamp) pair is a
    trustworthy blame/audit trail. Lives in PlatBot's own dedicated database; the `platbot_`
    table prefix is a harmless naming convention (and keeps things unambiguous if the DB is
    ever consolidated with another service).
    """

    __tablename__ = 'platbot_mod_notes'

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    target_user_id: Mapped[int] = mapped_column(BigInteger)
    author_id: Mapped[int] = mapped_column(BigInteger)
    # Denormalized so blame survives even if the author later leaves the server.
    author_name: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index('ix_platbot_mod_notes_guild_target', 'guild_id', 'target_user_id'),
    )
