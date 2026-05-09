"""Voice service — TwiML generation and call lifecycle management.

Connects inbound Twilio calls to the ElevenLabs conversational agent via
a WebSocket media stream. Custom Stream Parameters are passed so ElevenLabs
can include caller_phone and call_sid in every tool call body.
"""

import asyncio
from typing import Optional

from twilio.twiml.voice_response import Connect, Stream, VoiceResponse

from app.repositories import RedisSessionRepository
from app.services.session_service import SessionService
from app.shared import get_logger

logger = get_logger("voice_service")

# ElevenLabs Twilio WebSocket endpoint (verified against ElevenLabs docs).
# agent_id identifies which ElevenLabs agent handles the call.
_ELEVENLABS_TWILIO_WS = "wss://api.elevenlabs.io/v1/convai/twilio"


class VoiceService:
    """Handles inbound call TwiML and call status tracking."""

    def __init__(
        self,
        agent_id: str,
        session_service: SessionService,
    ) -> None:
        self._agent_id = agent_id
        self._session = session_service

    def generate_inbound_twiml(self, call_sid: str, caller_phone: str) -> str:
        """Return TwiML XML that streams the call to the ElevenLabs agent.

        Passes caller_phone and call_sid as Stream Parameters so the agent
        can include them in tool call bodies (needed for WhatsApp delivery).
        """
        response = VoiceResponse()
        connect = Connect()

        stream = Stream(url=f"{_ELEVENLABS_TWILIO_WS}?agent_id={self._agent_id}")
        stream.parameter(name="caller_phone", value=caller_phone)
        stream.parameter(name="call_sid", value=call_sid)

        connect.append(stream)
        response.append(connect)

        twiml = str(response)
        logger.info("Generated TwiML for call_sid=%s caller=%s", call_sid, caller_phone)
        logger.debug("TwiML: %s", twiml)
        return twiml

    async def on_call_initiated(self, call_sid: str, caller_phone: str) -> None:
        """Initialize Redis session when an inbound call arrives."""
        await self._session.initialize_session(
            conversation_id=call_sid,
            user_phone=caller_phone,
        )
        logger.info("Session initialized: call_sid=%s phone=%s", call_sid, caller_phone)

    async def on_call_ended(self, call_sid: str, status: str, duration: Optional[str] = None) -> None:
        """Mark session ended and log final call status."""
        session = await self._session.end_session(call_sid)
        logger.info(
            "Call ended: call_sid=%s status=%s duration=%ss session_found=%s",
            call_sid, status, duration, session is not None,
        )
