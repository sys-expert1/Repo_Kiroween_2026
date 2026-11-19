"""SQLAlchemy engine, session factory and ORM tables."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class RepositoryRow(Base):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    source_type: Mapped[str] = mapped_column(String(16))
    location: Mapped[str] = mapped_column(String(4096))
    signals_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    def signals_dict(self) -> dict | None:
        return json.loads(self.signals_json) if self.signals_json else None


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_id: Mapped[str] = mapped_column(String(36), index=True)
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24))
    phase: Mapped[str | None] = mapped_column(String(24), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RunEventRow(Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    type: Mapped[str] = mapped_column(String(64))
    data_json: Mapped[str] = mapped_column(Text, default="{}")


class ApprovalRow(Base):
    """Audit record of every approval decision."""

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    decision: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


def make_engine(url: str) -> Engine:
    return create_engine(url, connect_args={"check_same_thread": False})


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
