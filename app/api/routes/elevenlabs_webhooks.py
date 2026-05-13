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
    """ElevenLabs calls this at the start of every Twilio inbound call.

    We return dynamic variables ElevenLabs injects into the agent prompt and tool
    calls: `caller_phone` for tool routing, and `previous_conversations` — the
    formatted recent-history string Ria uses to recognise returning customers.
    """
    body = await request.json()
    logger.info("Conversation initiation: %s", body)
    caller_id: str = body.get("caller_id", "")

    previous_conversations = ""
    try:
        from app.api.container import container
        if container and container.memory_service and caller_id:
            previous_conversations = container.memory_service.recent_for_prompt(caller_id, limit=3)
            if previous_conversations:
                logger.info("Injecting prior history for %s (%d chars)", caller_id, len(previous_conversations))
    except Exception as exc:
        logger.error("Failed to fetch prior history for %s: %s", caller_id, exc)

    return {
        "type": "conversation_initiation_client_data",
        "dynamic_variables": {
            "caller_phone": caller_id,
            "previous_conversations": previous_conversations,
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

    # Cross-call memory: fire-and-forget. Pull the Redis session synchronously now
    # (so it's not deleted before the task reads it), then spawn a background task
    # that calls Gemini and writes to Postgres. The webhook returns 200 immediately
    # so ElevenLabs doesn't retry.
    if container and container._session and container.memory_service:
        try:
            raw_session = await container._session.get_raw_session(conversation_id)
            import asyncio
            asyncio.create_task(container.memory_service.summarize_and_save(data, raw_session))
        except Exception as exc:
            logger.error("Failed to spawn memory summarisation task for %s: %s", conversation_id, exc)

    # Also keep the existing in-Redis finalisation (transcript + status) so the
    # session has the wrapped-up state if anything else reads it.
    if container and container._session:
        try:
            raw = await container._session.get_raw_session(conversation_id)
            recommended = raw.get("recommended_products_full") or []
            summary_record = {
                "conversation_id": conversation_id,
                "agent_id": agent_id,
                "status": status,
                "duration_secs": duration_secs,
                "customer_phone": raw.get("user_phone"),
                "captured_preferences": {
                    "occasion": raw.get("occasion"),
                    "recipient": raw.get("recipient"),
                    "metal_preference": raw.get("metal_preference"),
                    "budget_min": raw.get("budget_min"),
                    "budget_max": raw.get("budget_max"),
                },
                "recommended_products": [
                    {"id": p.get("id"), "name": p.get("name"), "price": p.get("price")} for p in recommended
                ],
                "elevenlabs_summary": analysis.get("transcript_summary"),
                "call_successful": analysis.get("call_successful"),
            }
            logger.info("Post-call summary record for %s: %s", conversation_id, summary_record)

            # Write the final record back into the session (TTL still applies) so
            # anything reading the session post-call sees the wrapped-up state.
            await container._session.update_context(
                conversation_id,
                {
                    "transcript": formatted,
                    "call_status": status,
                    "call_duration_secs": duration_secs,
                    "elevenlabs_summary": analysis.get("transcript_summary"),
                    "ended": True,
                },
            )
            if raw:
                logger.info("Session finalised for conversation_id=%s", conversation_id)
            else:
                logger.info(
                    "No prior session for conversation_id=%s — created a finalised record", conversation_id,
                )
        except Exception as exc:
            logger.error("Failed to finalise session for %s: %s", conversation_id, exc)

    return {"status": "ok"}
