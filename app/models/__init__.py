import uuid
from datetime import datetime, timezone
from sqlalchemy import JSON, DateTime, ForeignKey, String, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def uid():
    return uuid.uuid4().hex


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"
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
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    block_id: Mapped[str | None]
    page_number: Mapped[int | None]
    code: Mapped[str]
    severity: Mapped[str] = mapped_column(default="error")
    stage: Mapped[str]
    message: Mapped[str]
    resolution: Mapped[str] = mapped_column(default="manual_review_required")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    operation: Mapped[str]
    status: Mapped[str] = mapped_column(default="queued")
    progress: Mapped[float] = mapped_column(default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None]
    error_message: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    kind: Mapped[str]
    path: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def database(path):
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 30})
    @event.listens_for(engine, "connect")
    def pragmas(connection, _):
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)
