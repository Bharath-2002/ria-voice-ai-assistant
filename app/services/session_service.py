"""Session management service — thin orchestration over RedisSessionRepository."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.entities import ConversationContext
from app.repositories import RedisSessionRepository
from app.shared import get_logger

logger = get_logger("session_service")


class SessionService:
    """Manages per-conversation session state stored in Redis."""

    def __init__(self, repo: RedisSessionRepository) -> None:
        self._repo = repo

    async def initialize_session(self, conversation_id: str, user_phone: str) -> bool:
        """Create a fresh session for a new call."""
        session: Dict[str, Any] = {
            "conversation_id": conversation_id,
            "user_phone": user_phone,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "occasion": None,
            "recipient": None,
            "metal_preference": None,
            "budget_min": None,
            "budget_max": None,
            "recommended_products": [],
        }
        logger.info("Initializing session: %s", conversation_id)
        return await self._repo.set_session(conversation_id, session)

    async def get_raw_session(self, conversation_id: str) -> Dict[str, Any]:
        """Return the raw session dict (empty dict if not found)."""
        return await self._repo.get_session(conversation_id) or {}

    async def get_context(self, conversation_id: str) -> Optional[ConversationContext]:
        """Return the current conversation context, or None if session not found."""
        session = await self._repo.get_session(conversation_id)
        if not session:
            return None
        return ConversationContext(
            occasion=session.get("occasion"),
            recipient=session.get("recipient"),
            metal_preference=session.get("metal_preference"),
            budget_min=session.get("budget_min"),
            budget_max=session.get("budget_max"),
            recommended_products=session.get("recommended_products", []),
        )

    async def update_context(self, conversation_id: str, updates: Dict[str, Any]) -> bool:
        """Merge updates into the existing session (creates session if missing)."""
        session = await self._repo.get_session(conversation_id) or {}
        session.update(updates)
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        return await self._repo.set_session(conversation_id, session)

    async def end_session(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Mark session as ended and return full session data for persistence."""
        session = await self._repo.get_session(conversation_id)
        if not session:
            logger.warning("end_session: session not found for %s", conversation_id)
            return None
        session["ended_at"] = datetime.now(timezone.utc).isoformat()
        await self._repo.set_session(conversation_id, session)
        return session
