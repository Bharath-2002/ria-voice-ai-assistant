"""Cross-call memory service.

Two responsibilities:
  1. After each call ends, summarise it with Gemini and persist the customer +
     conversation row. Runs as a background task off the post-call webhook so the
     webhook itself returns instantly.
  2. Before each new call starts, fetch the last N conversation summaries for the
     caller's phone and format them as a prompt-friendly string that's injected
     into the agent via the `previous_conversations` dynamic variable.

The summary is purpose-built for the next-call use case — short, names specific
products, captures the outcome — not a transcript replacement (ElevenLabs owns
that).
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.repositories import MemoryRepository, ConversationRecord
from app.services.phone import normalize_phone
from app.shared import get_logger

logger = get_logger("memory_service")


_VALID_OUTCOMES = {
    "browsing", "card_sent", "store_inquired", "callback_requested", "declined", "other",
}


class MemoryService:
    """Summarise finished calls + serve recent history to upcoming calls."""

    def __init__(self, repo: MemoryRepository, gemini_api_key: str, judge_model: str = "gemini-2.5-flash") -> None:
        self._repo = repo
        self._api_key = gemini_api_key
        self._model = judge_model

    # =========================================================== read side =====

    def recent_for_prompt(self, phone: str, limit: int = 3) -> str:
        """Return the last `limit` conversation summaries for `phone`, formatted for the
        agent's `previous_conversations` dynamic variable. Empty string => new customer.
        """
        phone = normalize_phone(phone)
        if not phone:
            return ""
        try:
            rows = self._repo.recent_conversations_for_phone(phone, limit=limit)
        except Exception as exc:
            logger.error("recent_for_prompt failed for %s: %s", phone, exc)
            return ""
        if not rows:
            return ""

        lines: List[str] = []
        now = datetime.now(timezone.utc)
        for r in rows:
            when = _humanise_relative(r.ended_at, now) if r.ended_at else "previously"
            tag = f" ({r.outcome})" if r.outcome else ""
            lines.append(f"- {when}{tag}: {(r.summary or '').strip()}")
        cust = self._repo.get_customer_by_phone(phone)
        header = f"Returning customer{' — ' + cust.name if cust and cust.name else ''}. Recent calls (most recent first):"
        return header + "\n" + "\n".join(lines)

    # =========================================================== write side ====

    async def summarize_and_save(self, detail: Dict[str, Any], redis_session: Dict[str, Any]) -> None:
        """Background task: produce a structured summary of a finished call and persist it.

        `detail` is ElevenLabs' post-call payload's `data` block (transcript, analysis, metadata).
        `redis_session` is the per-call session dict (preferences, recommended products, etc).
        All errors are caught and logged — this must not break the webhook handler.
        """
        try:
            await self._summarize_and_save_unchecked(detail, redis_session)
        except Exception as exc:
            logger.error("summarize_and_save failed: %s", exc, exc_info=True)

    async def _summarize_and_save_unchecked(self, detail: Dict[str, Any], redis_session: Dict[str, Any]) -> None:
        conversation_id = detail.get("conversation_id") or "unknown"
        agent_id = detail.get("agent_id") or ""

        metadata = detail.get("metadata") or {}
        phone_call = metadata.get("phone_call") or {}
        direction = phone_call.get("direction") or detail.get("call_direction") or "unknown"
        # On inbound, external_number = caller; on outbound, external_number = the dialed customer.
        # Either way, that's the customer's number from our point of view.
        raw_phone = (
            phone_call.get("external_number")
            or redis_session.get("user_phone")
            or ""
        )
        phone = normalize_phone(raw_phone)
        if not phone:
            logger.warning("summarize_and_save: no usable customer phone for %s — skipping", conversation_id)
            return

        transcript_turns = detail.get("transcript") or []
        analysis = detail.get("analysis") or {}
        elevenlabs_summary = analysis.get("transcript_summary") or ""
        duration_secs = metadata.get("call_duration_secs")
        started_unix = metadata.get("start_time_unix_secs")
        started_at = (
            datetime.fromtimestamp(started_unix, tz=timezone.utc) if started_unix else None
        )

        formatted_transcript = _format_transcript(transcript_turns)

        # 1) ask Gemini for the structured summary
        gem = await asyncio.to_thread(
            self._gemini_summary,
            formatted_transcript=formatted_transcript,
            captured_preferences=_collect_prefs(redis_session),
            recommended_products=_collect_recommended(redis_session),
            cards_sent=redis_session.get("sent_design_ids") or [],
            elevenlabs_summary=elevenlabs_summary,
        )

        summary_text = (gem.get("summary") or "").strip()
        outcome = (gem.get("outcome") or "").strip().lower()
        if outcome not in _VALID_OUTCOMES:
            outcome = "other" if summary_text else None
        follow_up = (gem.get("follow_up") or None)
        if isinstance(follow_up, str) and not follow_up.strip():
            follow_up = None
        customer_name = (gem.get("customer_name") or None)
        if isinstance(customer_name, str) and not customer_name.strip():
            customer_name = None

        if not summary_text:
            # never insert a row with empty summary — fall back to a one-line note
            summary_text = elevenlabs_summary or f"Call {conversation_id} ({duration_secs}s, {len(transcript_turns)} turns)."

        # 2) upsert customer (sets/keeps name) and the conversation row
        await asyncio.to_thread(self._persist, dict(
            phone=phone,
            customer_name=customer_name,
            conversation_id=conversation_id,
            agent_id=agent_id,
            direction=direction,
            started_at=started_at,
            duration_secs=duration_secs,
            summary=summary_text,
            outcome=outcome,
            follow_up=follow_up,
            captured_preferences=_collect_prefs(redis_session),
            recommended_products=_collect_recommended(redis_session),
            cards_sent=redis_session.get("sent_design_ids") or [],
            raw_summary_elevenlabs=elevenlabs_summary or None,
            raw_transcript_turns=len(transcript_turns),
        ))
        logger.info("Memory persisted: phone=%s conv=%s outcome=%s", phone, conversation_id, outcome)

    # -------------------------------------------------------- internals (sync) --

    def _persist(self, p: Dict[str, Any]) -> None:
        customer = self._repo.upsert_customer(phone=p["phone"], name=p.get("customer_name"))
        self._repo.save_conversation(
            customer_id=customer.id,
            conversation_id=p["conversation_id"],
            agent_id=p["agent_id"],
            direction=p["direction"],
            started_at=p["started_at"],
            duration_secs=p["duration_secs"],
            summary=p["summary"],
            outcome=p["outcome"],
            follow_up=p["follow_up"],
            captured_preferences=p["captured_preferences"],
            recommended_products=p["recommended_products"],
            cards_sent=p["cards_sent"],
            raw_summary_elevenlabs=p["raw_summary_elevenlabs"],
            raw_transcript_turns=p["raw_transcript_turns"],
        )

    def _gemini_summary(
        self,
        *,
        formatted_transcript: str,
        captured_preferences: Dict[str, Any],
        recommended_products: List[Dict[str, Any]],
        cards_sent: List[int],
        elevenlabs_summary: str,
    ) -> Dict[str, Any]:
        """Call Gemini with a strict JSON schema. Returns {summary, outcome, follow_up, customer_name}."""
        if not self._api_key:
            logger.warning("GEMINI_API_KEY not set — skipping LLM summary, using ElevenLabs' summary as fallback")
            return {"summary": elevenlabs_summary, "outcome": "other", "follow_up": None, "customer_name": None}

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self._api_key)
        prompt = (
            "You are summarising a finished jewellery-consultancy phone call between Ria "
            "(an AI agent at BlueStone Jewellery) and a customer. The summary will be injected "
            "into the agent's prompt the *next* time this customer calls, so it can pick the "
            "conversation back up specifically. Be concise (2-3 sentences), name products by "
            "name where they came up, and capture what the customer wanted, what was recommended, "
            "and what they decided (if anything).\n\n"
            f"TRANSCRIPT:\n{formatted_transcript[:6000]}\n\n"
            f"WHAT THE AGENT CAPTURED IN-SESSION:\n"
            f"- preferences: {json.dumps(captured_preferences)}\n"
            f"- recommended products: {json.dumps(recommended_products)[:1500]}\n"
            f"- design_ids sent to WhatsApp: {cards_sent}\n\n"
            f"ELEVENLABS' OWN SUMMARY (for reference): {elevenlabs_summary}\n\n"
            "Return JSON with exactly these fields:\n"
            "  summary: string  — 2-3 sentences, specific. Aimed at helping Ria pick up next time.\n"
            "  outcome: string  — one of: browsing | card_sent | store_inquired | callback_requested | declined | other\n"
            "  follow_up: string | null — the customer's request to be contacted later, in their phrasing "
            "(e.g. 'call me back next Tuesday afternoon'), or null if no such ask.\n"
            "  customer_name: string | null — if the customer clearly stated their name, return it (capitalised), else null.\n"
        )
        try:
            resp = client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0),
            )
            raw = resp.text or "{}"
        except Exception as exc:
            logger.error("Gemini summary call failed: %s", exc)
            return {"summary": elevenlabs_summary, "outcome": "other", "follow_up": None, "customer_name": None}

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.S)
            parsed = json.loads(m.group(0)) if m else {}
        return {
            "summary": parsed.get("summary") or elevenlabs_summary,
            "outcome": parsed.get("outcome") or "other",
            "follow_up": parsed.get("follow_up"),
            "customer_name": parsed.get("customer_name"),
        }


# =============================================================== helpers ====

def _format_transcript(turns: List[Dict[str, Any]]) -> str:
    out: List[str] = []
    for t in turns:
        role = t.get("role", "")
        who = "RIA" if role in ("agent", "assistant") else "CUSTOMER" if role in ("user", "customer") else role.upper()
        msg = (t.get("message") or "").strip()
        if msg:
            out.append(f"{who}: {msg}")
    return "\n".join(out)


def _collect_prefs(session: Dict[str, Any]) -> Dict[str, Any]:
    return {
        k: session.get(k)
        for k in ("occasion", "recipient", "metal_preference", "budget_min", "budget_max")
        if session.get(k) is not None
    }


def _collect_recommended(session: Dict[str, Any]) -> List[Dict[str, Any]]:
    full = session.get("recommended_products_full") or []
    return [
        {"id": p.get("id"), "name": p.get("name"), "price": p.get("price")}
        for p in full
        if isinstance(p, dict)
    ]


def _humanise_relative(when: datetime, now: datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    delta = now - when
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    if days < 7:
        return f"{days} day{'s' if days != 1 else ''} ago"
    weeks = days // 7
    if weeks < 5:
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"
    return when.strftime("%-d %b %Y")
