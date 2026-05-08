"""Conversation entity and related data models."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class ConversationStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ConversationContext:
    """Customer preferences gathered during the conversation."""

    occasion: Optional[str] = None
    recipient: Optional[str] = None
    metal_preference: Optional[str] = None
    budget_min: Optional[int] = None
    budget_max: Optional[int] = None
    recommended_products: List[str] = field(default_factory=list)


@dataclass
class Conversation:
    """Conversation entity."""

    conversation_id: str
    user_phone: str
    started_at: datetime
    status: ConversationStatus = ConversationStatus.ACTIVE
    context: ConversationContext = field(default_factory=ConversationContext)
    transcript: Optional[str] = None
    ended_at: Optional[datetime] = None
    summary: Optional[Dict] = None

    @property
    def duration_seconds(self) -> Optional[int]:
        """Elapsed call duration in whole seconds, or None if still active."""
        if self.ended_at:
            return int((self.ended_at - self.started_at).total_seconds())
        return None
