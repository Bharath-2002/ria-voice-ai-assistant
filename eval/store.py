"""Postgres persistence for evaluation runs (SQLAlchemy).

One table: `evaluations`. Re-validating a call inserts a new row (history kept);
queries return the latest row per conversation_id.

DATABASE_URL must point at a Postgres instance, e.g.
  postgresql+psycopg2://user:pass@host:5432/dbname
(A plain postgresql:// URL is upgraded to the psycopg2 driver automatically.)
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, Integer, String, create_engine, desc, select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()
_JSONType = JSONB().with_variant(JSON(), "sqlite")  # JSONB on PG, JSON elsewhere


class Evaluation(Base):
    __tablename__ = "evaluations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String, index=True, nullable=False)
    agent_id = Column(String, default="")
    direction = Column(String, default="unknown")          # inbound | outbound | unknown
    validated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    judge_model = Column(String, default="")
    overall_passed = Column(Boolean, default=False)
    overall_score = Column(Integer, default=0)
    dim_conversation = Column(Integer, default=0)
    dim_tool = Column(Integer, default=0)
    dim_business = Column(Integer, default=0)
    dim_voice = Column(Integer, default=0)
    results = Column(_JSONType, default=list)              # [{dimension,name,passed,score,na,reasoning,...}]
    transcript_snapshot = Column(_JSONType, default=list)
    tool_calls_snapshot = Column(_JSONType, default=list)
    post_call_summary = Column(String, default="")
    dim_passed = Column(_JSONType, default=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "agent_id": self.agent_id,
            "direction": self.direction,
            "validated_at": self.validated_at.isoformat() if self.validated_at else None,
            "judge_model": self.judge_model,
            "overall_passed": self.overall_passed,
            "overall_score": self.overall_score,
            "dim_conversation": self.dim_conversation,
            "dim_tool": self.dim_tool,
            "dim_business": self.dim_business,
            "dim_voice": self.dim_voice,
            "dim_passed": self.dim_passed or {},
            "results": self.results or [],
            "transcript_snapshot": self.transcript_snapshot or [],
            "tool_calls_snapshot": self.tool_calls_snapshot or [],
            "post_call_summary": self.post_call_summary or "",
        }


def _db_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    if url.startswith("postgres://"):  # Railway sometimes uses this scheme
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    return url


_engine = None
_Session = None


def _session():
    global _engine, _Session
    if _engine is None:
        _engine = create_engine(_db_url(), pool_pre_ping=True, future=True)
        Base.metadata.create_all(_engine)
        _Session = sessionmaker(bind=_engine, future=True)
    return _Session()


def save_evaluation(payload: Dict[str, Any]) -> int:
    """Insert one evaluation row from a validator.validate() payload. Returns the row id."""
    with _session() as s:
        row = Evaluation(
            conversation_id=payload["conversation_id"],
            agent_id=payload.get("agent_id", ""),
            direction=payload.get("direction", "unknown"),
            judge_model=payload.get("judge_model", ""),
            overall_passed=payload.get("overall_passed", False),
            overall_score=payload.get("overall_score", 0),
            dim_conversation=payload.get("dim_conversation", 0),
            dim_tool=payload.get("dim_tool", 0),
            dim_business=payload.get("dim_business", 0),
            dim_voice=payload.get("dim_voice", 0),
            results=payload.get("results", []),
            transcript_snapshot=payload.get("transcript_snapshot", []),
            tool_calls_snapshot=payload.get("tool_calls_snapshot", []),
            post_call_summary=payload.get("post_call_summary") or "",
            dim_passed=payload.get("dim_passed", {}),
        )
        s.add(row)
        s.commit()
        return row.id


def latest_by_conversation() -> Dict[str, Dict[str, Any]]:
    """Return {conversation_id: latest evaluation dict} across all evaluations."""
    with _session() as s:
        rows = s.execute(select(Evaluation).order_by(desc(Evaluation.validated_at))).scalars().all()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        out.setdefault(r.conversation_id, r.as_dict())
    return out


def get_evaluation(conversation_id: str) -> Optional[Dict[str, Any]]:
    with _session() as s:
        row = s.execute(
            select(Evaluation).where(Evaluation.conversation_id == conversation_id)
            .order_by(desc(Evaluation.validated_at)).limit(1)
        ).scalars().first()
        return row.as_dict() if row else None


def all_evaluations() -> List[Dict[str, Any]]:
    with _session() as s:
        rows = s.execute(select(Evaluation).order_by(desc(Evaluation.validated_at))).scalars().all()
        return [r.as_dict() for r in rows]
