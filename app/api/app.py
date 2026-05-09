"""FastAPI application factory with lifespan-based dependency management."""

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# load_dotenv must run before load_config reads os.environ
load_dotenv()

from app.shared import configure_logging, get_logger, load_config  # noqa: E402

logger = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize on startup, clean up on shutdown."""
    import app.api.container as _container_module

    config = load_config()
    configure_logging(config.log_level)
    logger.info("Starting BlueStone Voice Assistant...")

    container = _container_module.AppContainer(config)
    await container.initialize()
    _container_module.container = container
    logger.info("Application ready")

    yield

    logger.info("Shutting down...")
    await container.shutdown()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="BlueStone Voice Assistant",
        version="1.0.0",
        description="Voice AI jewelry consultant — Ria — powered by ElevenLabs + Twilio",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from app.api.routes.tools import router as tools_router
    from app.api.routes.webhooks import router as webhooks_router
    from app.api.routes.elevenlabs_webhooks import router as elevenlabs_router
    app.include_router(tools_router)
    app.include_router(webhooks_router)
    app.include_router(elevenlabs_router)

    @app.get("/health", tags=["health"])
    async def health():
        return {"status": "ok", "service": "bluestone-voice-assistant"}

    return app


app = create_app()
