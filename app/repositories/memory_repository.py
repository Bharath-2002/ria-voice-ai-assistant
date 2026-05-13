"""Cross-call memory repository — customers + conversations in Postgres.

Two tables:
  customers      — the customer entity, keyed by normalised phone.
  conversations  — one row per call. Carries the LLM-generated summary that gets
                   injected into the customer's *next* call.

This module owns its own SQLAlchemy Base so Alembic can manage just these tables
(the eval framework's `evaluations` table has its own Base in eval/store.py).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, String,
    Text, create_engine, desc, func, select,
)
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.engine import Engine

logger = logging.getLogger("bluestone.memory_repository")

MemoryBase = declarative_base()


class Customer(MemoryBase):
    __tablename__ = "customers"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    phone = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=True)
    first_seen = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    conversations = relationship("ConversationRecord", back_populates="customer", cascade="all, delete-orphan")


class ConversationRecord(MemoryBase):
    __tablename__ = "conversations"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    customer_id = Column(BigInteger, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)

    conversation_id = Column(String, unique=True, nullable=True)   # ElevenLabs id; upsert key
    agent_id = Column(String, nullable=True)
    direction = Column(String, nullable=True)                       # inbound | outbound
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    duration_secs = Column(Integer, nullable=True)

    summary = Column(Text, nullable=False)
    outcome = Column(String, nullable=True)                         # see eval/rubric.py-style enum (free text for now)
    follow_up = Column(Text, nullable=True)                          # raw phrasing; parsed later

    captured_preferences = Column(JSONB, nullable=False, server_default="{}")
    recommended_products = Column(JSONB, nullable=False, server_default="[]")
    cards_sent = Column(JSONB, nullable=False, server_default="[]")

    raw_summary_elevenlabs = Column(Text, nullable=True)
    raw_transcript_turns = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    customer = relationship("Customer", back_populates="conversations")


Index("idx_conversations_customer_recent", ConversationRecord.customer_id, ConversationRecord.ended_at.desc())


# --------------------------------------------------------------------- helpers

def _normalise_db_url(url: str) -> str:
    """Make sure SQLAlchemy uses the psycopg2 driver regardless of how Railway names it."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    return url


class MemoryRepository:
    """Persistence for customers + conversations. Engine is held here; sessions are short-lived."""

    def __init__(self, database_url: str) -> None:
        self._engine: Engine = create_engine(_normalise_db_url(database_url), pool_pre_ping=True, future=True)
        self._SessionLocal = sessionmaker(bind=self._engine, future=True, expire_on_commit=False)

    # ------------------------------------------------------------------ writes

    def upsert_customer(self, *, phone: str, name: Optional[str] = None) -> Customer:
        """Find or create the customer for `phone`. If `name` is given and we don't
        already have one stored, set it; otherwise keep what's there. Bumps last_seen.
        """
        if not phone:
            raise ValueError("phone is required")
        stmt = (
            pg_insert(Customer)
            .values(phone=phone, name=name)
            .on_conflict_do_update(
                index_elements=[Customer.phone],
                set_={
                    "last_seen": func.now(),
                    "updated_at": func.now(),
                    "name": func.coalesce(Customer.name, pg_insert(Customer).excluded.name),
                },
            )
            .returning(Customer)
        )
        with self._SessionLocal() as s:
            row = s.execute(stmt).scalar_one()
            s.commit()
            return row

    def save_conversation(self, *, customer_id: int, conversation_id: Optional[str], **fields: Any) -> ConversationRecord:
        """Upsert a conversation row by conversation_id (idempotent for retried webhooks)."""
        payload = {"customer_id": customer_id, "conversation_id": conversation_id, **fields}
        with self._SessionLocal() as s:
            if conversation_id:
                stmt = (
                    pg_insert(ConversationRecord)
                    .values(**payload)
                    .on_conflict_do_update(
                        index_elements=[ConversationRecord.conversation_id],
                        set_={k: v for k, v in payload.items() if k != "conversation_id"},
                    )
                    .returning(ConversationRecord)
                )
                row = s.execute(stmt).scalar_one()
            else:
                # No conversation_id (shouldn't normally happen) — just insert.
                row = ConversationRecord(**payload)
                s.add(row)
            s.commit()
            return row

    # ------------------------------------------------------------------- reads

    def recent_conversations_for_phone(self, phone: str, limit: int = 3) -> List[ConversationRecord]:
        if not phone:
            return []
        with self._SessionLocal() as s:
            stmt = (
                select(ConversationRecord)
                .join(Customer, Customer.id == ConversationRecord.customer_id)
                .where(Customer.phone == phone)
                .order_by(desc(ConversationRecord.ended_at))
                .limit(limit)
            )
            rows = s.execute(stmt).scalars().all()
            return list(rows)

    def get_customer_by_phone(self, phone: str) -> Optional[Customer]:
        if not phone:
            return None
        with self._SessionLocal() as s:
            return s.execute(select(Customer).where(Customer.phone == phone)).scalar_one_or_none()

    # ---------------------------------------------------------------- lifecycle

    def dispose(self) -> None:
        self._engine.dispose()
