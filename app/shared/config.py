"""Configuration management — loads and validates environment variables."""

from dataclasses import dataclass
from functools import lru_cache
from os import environ

from app.shared.exceptions import ConfigurationError

_REQUIRED = [
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_PHONE_NUMBER",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_AGENT_ID",
]


@dataclass(frozen=True)
class AppConfig:
    """Immutable application configuration."""

    environment: str
    log_level: str
    # Twilio
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_phone_number: str
    twilio_whatsapp_number: str
    # ElevenLabs
    elevenlabs_api_key: str
    elevenlabs_agent_id: str
    elevenlabs_webhook_secret: str
    elevenlabs_phone_number_id: str
    # BlueStone
    bluestone_api_key: str
    bluestone_base_url: str
    # Redis
    redis_url: str
    redis_ttl_seconds: int


def load_config() -> AppConfig:
    """Load config from environment, raising ConfigurationError on missing required vars."""
    missing = [k for k in _REQUIRED if not environ.get(k)]
    if missing:
        raise ConfigurationError(f"Missing required environment variables: {', '.join(missing)}")

    return AppConfig(
        environment=environ.get("ENVIRONMENT", "development"),
        log_level=environ.get("LOG_LEVEL", "INFO"),
        twilio_account_sid=environ["TWILIO_ACCOUNT_SID"],
        twilio_auth_token=environ["TWILIO_AUTH_TOKEN"],
        twilio_phone_number=environ["TWILIO_PHONE_NUMBER"],
        twilio_whatsapp_number=environ.get("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886"),
        elevenlabs_api_key=environ["ELEVENLABS_API_KEY"],
        elevenlabs_agent_id=environ["ELEVENLABS_AGENT_ID"],
        elevenlabs_webhook_secret=environ.get("ELEVENLABS_WEBHOOK_SECRET", ""),
        elevenlabs_phone_number_id=environ.get("ELEVENLABS_PHONE_NUMBER_ID", ""),
        bluestone_api_key=environ.get("BLUESTONE_API_KEY", ""),
        bluestone_base_url=environ.get("BLUESTONE_BASE_URL", "https://www.bluestone.com"),
        redis_url=environ.get("REDIS_URL", "redis://localhost:6379/0"),
        redis_ttl_seconds=int(environ.get("REDIS_TTL_SECONDS", "86400")),
    )


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Return cached config (loaded once per process)."""
    return load_config()
