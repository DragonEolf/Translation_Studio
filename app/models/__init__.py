"""Persistent document state and additive SQLite integrity migrations."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)


def uid():
    return uuid.uuid4().hex


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"
    pages: Mapped[list["Page"]] = relationship(back_populates="document")
    blocks: Mapped[list["Block"]] = relationship(back_populates="document")
    jobs: Mapped[list["Job"]] = relationship(back_populates="document")
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    filename: Mapped[str]
    sha256: Mapped[str]
    status: Mapped[str] = mapped_column(default="queued")
    page_count: Mapped[int] = mapped_column(default=0)
    target_language: Mapped[str | None]
    script: Mapped[str] = mapped_column(default="native")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("document_id", "number", name="uq_pages_document_number"),
        CheckConstraint("number > 0 AND width > 0 AND height > 0"),
    )
    document: Mapped["Document"] = relationship(back_populates="pages")
    id: Mapped[str] = mapped_column(primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    number: Mapped[int]
    width: Mapped[float]
    height: Mapped[float]
    dirty: Mapped[bool] = mapped_column(default=True)
    extraction_status: Mapped[str] = mapped_column(default="completed")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Block(Base):
    __tablename__ = "blocks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "page_number"], ["pages.document_id", "pages.number"]
        ),
        Index("ix_blocks_page_order", "document_id", "page_number", "reading_order"),
    )
    document: Mapped["Document"] = relationship(back_populates="blocks")
    page: Mapped["Page"] = relationship(overlaps="blocks,document")
    translations: Mapped[list["Translation"]] = relationship(back_populates="block")
    id: Mapped[str] = mapped_column(primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    page_number: Mapped[int]
    type: Mapped[str]
    reading_order: Mapped[int]
    bbox: Mapped[list] = mapped_column(JSON)
    source_text: Mapped[str]
    style: Mapped[dict] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(default=0.8)


class Translation(Base):
    __tablename__ = "translations"
    __table_args__ = (
        UniqueConstraint("block_id", "revision", name="uq_translations_block_revision"),
        CheckConstraint("revision > 0"),
        CheckConstraint("script IN ('native', 'roman', 'codemix')"),
    )
    block: Mapped["Block"] = relationship(back_populates="translations")
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    block_id: Mapped[str] = mapped_column(ForeignKey("blocks.id"), index=True)
    target_language: Mapped[str]
    script: Mapped[str]
    text: Mapped[str]
    revision: Mapped[int]
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LayoutResult(Base):
    __tablename__ = "layouts"
    block_id: Mapped[str] = mapped_column(ForeignKey("blocks.id"), primary_key=True)
    bbox: Mapped[list] = mapped_column(JSON)
    font: Mapped[str]
    font_size: Mapped[float]
    line_breaks: Mapped[list] = mapped_column(JSON, default=list)
    fit_status: Mapped[str]


class Issue(Base):
    __tablename__ = "issues"
    __table_args__ = (
        Index("ix_issues_document_resolution", "document_id", "resolution"),
    )
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    block_id: Mapped[str | None] = mapped_column(ForeignKey("blocks.id"), index=True)
    page_number: Mapped[int | None]
    code: Mapped[str]
    severity: Mapped[str] = mapped_column(default="error")
    stage: Mapped[str]
    message: Mapped[str]
    resolution: Mapped[str] = mapped_column(default="manual_review_required")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("progress >= 0 AND progress <= 1"),
        Index("ix_jobs_status_created", "status", "created_at"),
    )
    document: Mapped["Document"] = relationship(back_populates="jobs")
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    operation: Mapped[str]
    status: Mapped[str] = mapped_column(default="queued")
    progress: Mapped[float] = mapped_column(default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None]
    error_message: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, onupdate=now
    )


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    kind: Mapped[str]
    path: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def database(path):
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 30}
    )

    @event.listens_for(engine, "connect")
    def pragmas(connection, _):
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_jobs_one_active_per_document ON jobs (document_id) WHERE status IN ('queued', 'running', 'cancelling')"
            )
        )
    # Additive migration: triggers enforce new invariants for existing SQLite
    # files too. No table rebuild, row deletion, or history rewriting is needed.
    with engine.begin() as connection:
        for name, table, columns in (
            ("uq_pages_document_number", "pages", "document_id, number"),
            ("uq_translations_block_revision", "translations", "block_id, revision"),
        ):
            connection.execute(
                text(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table} ({columns})")
            )
        for name, table, columns in (
            (
                "ix_blocks_page_order",
                "blocks",
                "document_id, page_number, reading_order",
            ),
            ("ix_issues_document_resolution", "issues", "document_id, resolution"),
            ("ix_jobs_status_created", "jobs", "status, created_at"),
        ):
            connection.execute(
                text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})")
            )
        statuses = "'queued','running','cancelling','cancelled','completed','completed_with_issues','failed'"
        rules = {
            "documents": f"NEW.status NOT IN ({statuses}) OR NEW.page_count < 0",
            "jobs": f"NEW.status NOT IN ({statuses}) OR NEW.progress < 0 OR NEW.progress > 1",
            "pages": "NEW.number <= 0 OR NEW.width <= 0 OR NEW.height <= 0",
            "blocks": "NOT EXISTS (SELECT 1 FROM pages WHERE document_id=NEW.document_id AND number=NEW.page_number)",
            "translations": "NEW.revision <= 0 OR NEW.script NOT IN ('native','roman','codemix')",
            "issues": "NEW.severity NOT IN ('warning','error') OR NEW.resolution NOT IN ('resolved','manual_review_required') OR (NEW.block_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM blocks WHERE id=NEW.block_id AND document_id=NEW.document_id AND (NEW.page_number IS NULL OR page_number=NEW.page_number)))",
        }
        for table, invalid in rules.items():
            for action in ("INSERT", "UPDATE"):
                connection.execute(
                    text(
                        f"CREATE TRIGGER IF NOT EXISTS validate_{table}_{action.lower()} BEFORE {action} ON {table} WHEN {invalid} BEGIN SELECT RAISE(ABORT, 'Invalid {table} record'); END"
                    )
                )
        # Existing tables lack the new foreign keys: guard parent key changes
        # and deletions as well as child writes, without rebuilding those tables.
        guards = (
            (
                "pages",
                "EXISTS (SELECT 1 FROM blocks WHERE document_id=OLD.document_id AND page_number=OLD.number)",
                "document_id, number",
            ),
            (
                "blocks",
                "EXISTS (SELECT 1 FROM issues WHERE block_id=OLD.id)",
                "id, document_id, page_number",
            ),
        )
        for table, referenced, keys in guards:
            for action in ("DELETE", f"UPDATE OF {keys}"):
                suffix = "delete" if action == "DELETE" else "keys"
                connection.execute(
                    text(
                        f"CREATE TRIGGER IF NOT EXISTS protect_{table}_{suffix} BEFORE {action} ON {table} WHEN {referenced} BEGIN SELECT RAISE(ABORT, 'Referenced {table} record'); END"
                    )
                )
    return engine, sessionmaker(engine, expire_on_commit=False)
