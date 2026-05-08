"""Redis-backed session repository."""

import json
from typing import Any, Dict, Optional

from redis.asyncio import Redis

from app.shared import get_logger

logger = get_logger("session_repository")


class RedisSessionRepository:
    """Stores and retrieves conversation session data in Redis.

    Errors are caught and logged here — callers receive None / False as safe
    defaults rather than exceptions, keeping session failures non-fatal.
    """

    def __init__(self, redis_url: str, ttl_seconds: int = 86400) -> None:
        self._url = redis_url
        self._ttl = ttl_seconds
        self._client: Optional[Redis] = None

    async def connect(self) -> None:
        """Open the Redis connection."""
        self._client = Redis.from_url(self._url, decode_responses=True)
        logger.info("Redis connection established")

    async def disconnect(self) -> None:
        """Close the Redis connection."""
        if self._client:
            await self._client.aclose()
            logger.info("Redis connection closed")

    async def get_session(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Return session dict or None if missing / on error."""
        if not self._client:
            logger.warning("get_session called before connect()")
            return None
        try:
            raw = await self._client.get(f"conversation:{conversation_id}")
            return json.loads(raw) if raw else None
        except Exception as exc:
            logger.error("Redis get error for %s: %s", conversation_id, exc)
            return None

    async def set_session(self, conversation_id: str, data: Dict[str, Any]) -> bool:
        """Persist session dict with TTL. Returns False on error."""
        if not self._client:
            logger.warning("set_session called before connect()")
            return False
        try:
            await self._client.setex(
                f"conversation:{conversation_id}",
                self._ttl,
                json.dumps(data),
            )
            return True
        except Exception as exc:
            logger.error("Redis set error for %s: %s", conversation_id, exc)
            return False

    async def delete_session(self, conversation_id: str) -> bool:
        """Delete a session. Returns False on error."""
        if not self._client:
            return False
        try:
            await self._client.delete(f"conversation:{conversation_id}")
            return True
        except Exception as exc:
            logger.error("Redis delete error for %s: %s", conversation_id, exc)
            return False
