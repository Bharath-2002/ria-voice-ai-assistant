"""ElevenLabs post-call webhook — transcript capture and conversation close."""

import hashlib
import hmac
import time
from typing import Any, Dict

from fastapi import APIRouter, Header, HTTPException, Request

from app.shared import get_logger

logger = get_logger("elevenlabs_webhooks")

router = APIRouter(prefix="/elevenlabs", tags=["elevenlabs"])


@router.post("/initiation")
async def conversation_initiation(request: Request) -> Dict[str, Any]:
    """ElevenLabs calls this at the start of every Twilio call.

    We receive the call metadata and return dynamic variables (including
    caller_phone) so ElevenLabs can inject them into tool calls.
    """
    body = await request.json()
    logger.info("Conversation initiation: %s", body)

    # ElevenLabs sends caller_id for Twilio calls
    caller_id: str = body.get("caller_id", "")

    return {
        "type": "conversation_initiation_client_data",
        "dynamic_variables": {
            "caller_phone": caller_id,
        },
    }

_MAX_TIMESTAMP_SKEW_SECS = 300  # reject replays older than 5 minutes


def _verify_signature(body: bytes, signature_header: str, secret: str) -> None:
    """Verify ElevenLabs HMAC-SHA256 webhook signature.

    Header format: t=<unix_timestamp>,v0=<hex_signature>
    Signed payload:  "<timestamp>.<raw_body>"
    """
    if not secret:
        logger.warning("ELEVENLABS_WEBHOOK_SECRET not set — skipping signature verification")
        return

    parts = dict(part.split("=", 1) for part in signature_header.split(",") if "=" in part)
    timestamp = parts.get("t")
    signature = parts.get("v0")

    if not timestamp or not signature:
        raise HTTPException(status_code=400, detail="Invalid signature header format")

    try:
        ts_int = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid timestamp in signature header")

    age = abs(time.time() - ts_int)
    if age > _MAX_TIMESTAMP_SKEW_SECS:
        raise HTTPException(status_code=400, detail=f"Webhook timestamp too old ({age:.0f}s)")

    signed_payload = f"{timestamp}.".encode() + body
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=403, detail="Webhook signature mismatch")


def _format_transcript(turns: list[Dict[str, Any]]) -> str:
    """Convert ElevenLabs transcript turns to a readable string."""
    lines = []
    for turn in turns:
        role = turn.get("role", "unknown").capitalize()
        message = (turn.get("message") or "").strip()
        if message:
            lines.append(f"{role}: {message}")
    return "\n".join(lines)


@router.post("/post-call")
async def post_call_webhook(
    request: Request,
    elevenlabs_signature: str = Header(default="", alias="ElevenLabs-Signature"),
) -> Dict[str, str]:
    """ElevenLabs calls this after every conversation ends.

    Verifies the HMAC signature, logs the full transcript, and updates
    the Redis session with the final conversation record.
    """
    raw_body = await request.body()

    from app.api.container import container
    secret = container.config.elevenlabs_webhook_secret if container else ""
    _verify_signature(raw_body, elevenlabs_signature, secret)

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event_type = payload.get("type")
    if event_type != "post_call_transcription":
        # ElevenLabs may send other event types in future; acknowledge silently
        logger.debug("Ignoring ElevenLabs webhook event type: %s", event_type)
        return {"status": "ignored"}

    data: Dict[str, Any] = payload.get("data", {})
    conversation_id: str = data.get("conversation_id", "unknown")
    agent_id: str = data.get("agent_id", "unknown")
    status: str = data.get("status", "unknown")
    transcript_turns: list = data.get("transcript", [])
    metadata: Dict = data.get("metadata", {})
    analysis: Dict = data.get("analysis", {})

    duration_secs = metadata.get("call_duration_secs")
    formatted = _format_transcript(transcript_turns)

    logger.info(
        "Post-call webhook: conversation_id=%s agent_id=%s status=%s duration=%ss turns=%d",
        conversation_id, agent_id, status, duration_secs, len(transcript_turns),
    )

    if formatted:
        logger.info("Transcript for %s:\n%s", conversation_id, formatted)
    else:
        logger.info("No transcript turns for conversation_id=%s", conversation_id)

    if analysis:
        logger.info("Analysis for %s: %s", conversation_id, analysis)

    # Persist transcript into the Redis session so it's available for Phase 11
    if container and container._session:
        try:
            session = await container._session.get_context(conversation_id)
            if session:
                await container._session.update_context(
                    conversation_id,
                    {
                        "transcript": formatted,
                        "call_status": status,
                        "call_duration_secs": duration_secs,
                    },
                )
                logger.info("Session updated with transcript: conversation_id=%s", conversation_id)
            else:
                logger.warning(
                    "No active session found for conversation_id=%s (call may have already ended)",
                    conversation_id,
                )
        except Exception as exc:
            logger.error("Failed to persist transcript for %s: %s", conversation_id, exc)

    return {"status": "ok"}
