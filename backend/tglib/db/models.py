from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Naive UTC timestamp; everything in the DB is UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class DocStatus:
    AVAILABLE = "available"  # found by a scan; downloaded only when requested
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    DUPLICATE = "duplicate"

    ALL = (AVAILABLE, PENDING, DOWNLOADING, DONE, FAILED, SKIPPED, DUPLICATE)


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    title: Mapped[str | None] = mapped_column(Text)
    username: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(String(16))  # channel / group / user
    tracked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Tracking only scans; with this on, new files are also queued for download.
    auto_download: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_scanned_msg_id: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # A scan pass that has not finished: the files from `scan_top_msg_id` down
    # to `scan_resume_msg_id` are recorded, and it continues below that.
    scan_top_msg_id: Mapped[int | None] = mapped_column(BigInteger)
    scan_resume_msg_id: Mapped[int | None] = mapped_column(BigInteger)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_scan_error: Mapped[str | None] = mapped_column(Text)
    allowed_exts: Mapped[str | None] = mapped_column(Text)
    max_size_mb: Mapped[int | None] = mapped_column(Integer)
    # False when the chat no longer shows up in the account's dialogs.
    is_member: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Blob(Base):
    """Unique file content on disk."""

    __tablename__ = "blobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)  # relative to STORAGE_DIR
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Document(Base):
    """Unique Telegram document (forwards share the same tg_document_id)."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_document_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    original_name: Mapped[str | None] = mapped_column(Text)
    normalized_name: Mapped[str | None] = mapped_column(Text)
    ext: Mapped[str | None] = mapped_column(String(32))
    mime: Mapped[str | None] = mapped_column(String(128))
    size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    blob_id: Mapped[int | None] = mapped_column(ForeignKey("blobs.id"))
    # Set when this document was judged a duplicate of another document by
    # name+size before download (the original may not have a blob yet).
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    # Multi-volume works: same series_key, different volume. '' = not a volume.
    series_key: Mapped[str | None] = mapped_column(Text)
    volume: Mapped[int | None] = mapped_column(Integer)
    bytes_downloaded: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    blob: Mapped[Blob | None] = relationship(lazy="raise")

    __table_args__ = (
        Index("ix_documents_status_priority", "status", "priority"),
        Index("ix_documents_size", "size"),
        Index("ix_documents_normalized_name", "normalized_name"),
        Index("ix_documents_duplicate_of_id", "duplicate_of_id"),
        Index("ix_documents_blob_id", "blob_id"),
        Index("ix_documents_series_key", "series_key"),
    )


class Appearance(Base):
    """A message in a chat that carries a document."""

    __tablename__ = "appearances"

    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), primary_key=True)
    msg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    caption: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_appearances_document_id", "document_id"),)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
