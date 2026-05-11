"""Twilio webhook routes — inbound calls, outbound calls, and status callbacks."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Form, HTTPException
from fastapi.responses import Response

from twilio.twiml.voice_response import Say, VoiceResponse

from app.services.voice_service import VoiceService
from app.shared import ServiceError, get_logger

logger = get_logger("webhooks_router")

router = APIRouter(prefix="/voice", tags=["voice"])


def _get_voice_service() -> VoiceService:
    from app.api.container import container
    return container.voice_service


@router.post("/outbound")
async def outbound_call(
    payload: Dict[str, Any] = Body(...),
    voice_service: VoiceService = Depends(_get_voice_service),
) -> Dict[str, Any]:
    """Trigger an outbound call: ElevenLabs dials `to_number` and connects Ria.

    Body: {"to_number": "+919385763994"}
    """
    to_number = (payload or {}).get("to_number", "").strip()
    if not to_number:
        raise HTTPException(status_code=422, detail="to_number is required (E.164, e.g. +919385763994)")
    if not to_number.startswith("+"):
        to_number = f"+{to_number}"

    try:
        result = await voice_service.initiate_outbound_call(to_number=to_number)
    except ServiceError as exc:
        logger.error("Outbound call error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))

    return {"status": "calling", "to_number": to_number, "elevenlabs": result}


@router.post("/inbound")
async def inbound_call(
    CallSid: str = Form(...),
    From: str = Form(...),
    To: str = Form(...),
    CallStatus: str = Form(default="ringing"),
    voice_service: VoiceService = Depends(_get_voice_service),
) -> Response:
    """Twilio calls this when someone dials our number.

    Initializes the session and returns TwiML that streams the call to
    the ElevenLabs agent via WebSocket.
    """
    logger.info("Inbound call: CallSid=%s From=%s To=%s Status=%s", CallSid, From, To, CallStatus)

    await voice_service.on_call_initiated(call_sid=CallSid, caller_phone=From)

    try:
        twiml = voice_service.generate_inbound_twiml(call_sid=CallSid, caller_phone=From)
    except Exception as exc:
        logger.error("Failed to generate TwiML for %s: %s", CallSid, exc)
        # Return a graceful spoken error instead of a 500 (Twilio reads it aloud)
        fallback = VoiceResponse()
        fallback.append(Say("Sorry, we're having a technical issue. Please try again in a moment."))
        return Response(content=str(fallback), media_type="application/xml")

    return Response(content=twiml, media_type="application/xml")


@router.post("/status")
async def call_status(
    CallSid: str = Form(...),
    CallStatus: str = Form(...),
    From: str = Form(default=""),
    To: str = Form(default=""),
    CallDuration: Optional[str] = Form(default=None),
    voice_service: VoiceService = Depends(_get_voice_service),
) -> Response:
    """Twilio calls this on every call status transition.

    Terminal statuses (completed, failed, busy, no-answer) close the session.
    """
    logger.info("Call status: CallSid=%s Status=%s Duration=%s", CallSid, CallStatus, CallDuration)

    terminal = {"completed", "failed", "busy", "no-answer", "canceled"}
    if CallStatus in terminal:
        await voice_service.on_call_ended(
            call_sid=CallSid,
            status=CallStatus,
            duration=CallDuration,
        )

    # Twilio expects an empty 200 response for status callbacks
    return Response(status_code=200)
