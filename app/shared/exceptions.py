"""Custom exception hierarchy."""


class BlueStoneError(Exception):
    """Base exception for the application."""


class ConfigurationError(BlueStoneError):
    """Raised when required configuration is missing or invalid."""


class SessionError(BlueStoneError):
    """Raised on session read/write failures."""


class ServiceError(BlueStoneError):
    """Raised when an external service call fails unrecoverably."""


class BlueStoneAPIError(ServiceError):
    """Raised when the BlueStone catalog API fails."""


class ElevenLabsAPIError(ServiceError):
    """Raised when the ElevenLabs API fails."""


class TwilioAPIError(ServiceError):
    """Raised when the Twilio API fails."""


class WebhookVerificationError(BlueStoneError):
    """Raised when a webhook signature cannot be verified."""


class ProductNotFoundError(BlueStoneError):
    """Raised when a requested product does not exist."""


class ConversationNotFoundError(BlueStoneError):
    """Raised when a conversation session cannot be found."""
