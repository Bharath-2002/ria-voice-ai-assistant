"""Dependency injection container — wires all layers together."""

import httpx

from app.features import ConversationFeature
from app.repositories import RedisSessionRepository
from app.services import BlueStoneService, SessionService
from app.shared import AppConfig, get_logger

logger = get_logger("container")


class AppContainer:
    """Holds and initializes all application dependencies."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._http_client: httpx.AsyncClient | None = None
        self._redis_repo: RedisSessionRepository | None = None
        self._bluestone: BlueStoneService | None = None
        self._session: SessionService | None = None
        self.conversation_feature: ConversationFeature | None = None

    async def initialize(self) -> None:
        """Boot all dependencies in bottom-up order."""
        logger.info("Initializing application container...")

        self._http_client = httpx.AsyncClient(timeout=8.0)
        logger.info("  HTTP client ready")

        self._redis_repo = RedisSessionRepository(
            redis_url=self.config.redis_url,
            ttl_seconds=self.config.redis_ttl_seconds,
        )
        await self._redis_repo.connect()
        logger.info("  Redis repository ready")

        self._bluestone = BlueStoneService(http_client=self._http_client)
        logger.info("  BlueStone service ready")

        self._session = SessionService(repo=self._redis_repo)
        logger.info("  Session service ready")

        self.conversation_feature = ConversationFeature(
            session_service=self._session,
            bluestone_service=self._bluestone,
        )
        logger.info("  ConversationFeature ready")
        logger.info("Container fully initialized")

    async def shutdown(self) -> None:
        """Gracefully close all connections."""
        logger.info("Shutting down container...")
        if self._bluestone:
            await self._bluestone.close()
        if self._redis_repo:
            await self._redis_repo.disconnect()
        if self._http_client:
            await self._http_client.aclose()
        logger.info("Container shutdown complete")


# Module-level singleton — populated during app lifespan startup
container: AppContainer | None = None
