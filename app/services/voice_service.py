"""Voice service — TwiML generation and call lifecycle management.

Connects inbound Twilio calls to the ElevenLabs conversational agent via
a WebSocket media stream using the /v1/convai/twilio endpoint, which accepts
Twilio's mulaw 8kHz audio format. The API key is passed as a query param
because Twilio media streams cannot send custom headers.

Custom Stream Parameters (caller_phone, call_sid) are injected so ElevenLabs
includes them in every tool call body (needed for WhatsApp delivery).
"""

from typing import Any, Dict, Optional

import httpx
from twilio.twiml.voice_response import Connect, Stream, VoiceResponse

from app.services.session_service import SessionService
from app.shared import ServiceError, get_logger

logger = get_logger("voice_service")

_OUTBOUND_CALL_URL = "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"


class VoiceService:
    """Handles inbound call TwiML, outbound call initiation, and call status."""

    def __init__(
        self,
        agent_id: str,
        elevenlabs_api_key: str,
        session_service: SessionService,
        http_client: httpx.AsyncClient,
        agent_phone_number_id: Optional[str] = None,
    ) -> None:
        self._agent_id = agent_id
        self._api_key = elevenlabs_api_key
        self._session = session_service
        self._http = http_client
        self._agent_phone_number_id = agent_phone_number_id

    def _check_api_key(self) -> None:
        """Log whether the ElevenLabs API key looks valid (startup diagnostic)."""
        if not self._api_key:
            logger.error("ELEVENLABS_API_KEY is not set")
        elif not self._api_key.startswith("sk_"):
            logger.warning("ELEVENLABS_API_KEY does not start with 'sk_' — may be invalid")
        else:
            logger.info("ElevenLabs API key loaded (sk_...%s)", self._api_key[-4:])

    def generate_inbound_twiml(self, call_sid: str, caller_phone: str) -> str:
        """Return TwiML XML that streams the call to the ElevenLabs agent.

        Uses the /v1/convai/twilio endpoint which accepts Twilio's mulaw
        8kHz audio format. The API key is passed as a query param because
        Twilio media streams cannot send custom headers.
        """
        ws_url = (
            f"wss://api.elevenlabs.io/v1/convai/twilio"
            f"?agent_id={self._agent_id}"
            f"&xi-api-key={self._api_key}"
        )

        response = VoiceResponse()
        connect = Connect()

        stream = Stream(url=ws_url)
        stream.parameter(name="caller_phone", value=caller_phone)
        stream.parameter(name="call_sid", value=call_sid)

        connect.append(stream)
        response.append(connect)

        twiml = str(response)
        logger.info("Generated TwiML for call_sid=%s caller=%s", call_sid, caller_phone)
        return twiml

    async def initiate_outbound_call(self, to_number: str) -> Dict[str, Any]:
        """Ask ElevenLabs to place an outbound call to `to_number` via Twilio.

        ElevenLabs dials the number using the imported Twilio phone line and
        connects the agent when the callee answers. caller_phone is available
        to tools as the system__caller_id dynamic variable (= to_number).
        """
        if not self._agent_phone_number_id:
            raise ServiceError("ELEVENLABS_PHONE_NUMBER_ID is not configured")

        payload = {
            "agent_id": self._agent_id,
            "agent_phone_number_id": self._agent_phone_number_id,
            "to_number": to_number,
            # On outbound calls system__caller_id is not the dialed number, so
            # surface the destination number explicitly for tool calls (WhatsApp).
            "conversation_initiation_client_data": {
                "dynamic_variables": {
                    "system__caller_id": to_number,
                    "caller_phone": to_number,
                },
            },
        }
        logger.info("Initiating outbound call to %s", to_number)
        resp = await self._http.post(
            _OUTBOUND_CALL_URL,
            headers={"xi-api-key": self._api_key},
            json=payload,
        )
        if resp.status_code >= 400:
            logger.error("Outbound call failed (%s): %s", resp.status_code, resp.text)
            raise ServiceError(f"ElevenLabs outbound call failed ({resp.status_code}): {resp.text}")

        data = resp.json()
        logger.info("Outbound call queued to %s: %s", to_number, data)
        return data

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
