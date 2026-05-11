"""Thin ElevenLabs Conversational AI API client — list and fetch conversations."""

import os
from typing import Any, Dict, List, Optional

import httpx

_BASE = "https://api.elevenlabs.io/v1/convai"


def _api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    return key


def _agent_id() -> Optional[str]:
    return os.environ.get("ELEVENLABS_AGENT_ID") or None


def list_conversations(limit: int = 100) -> List[Dict[str, Any]]:
    """Return recent conversations for the configured agent (newest first).

    Each item is a summary: conversation_id, start_time_unix_secs, call_duration_secs,
    message_count, status, call_successful, direction (inbound/outbound), agent_id, ...
    """
    params: Dict[str, Any] = {"page_size": min(limit, 100)}
    aid = _agent_id()
    if aid:
        params["agent_id"] = aid
    headers = {"xi-api-key": _api_key()}
    out: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    with httpx.Client(timeout=15.0) as client:
        while len(out) < limit:
            if cursor:
                params["cursor"] = cursor
            resp = client.get(f"{_BASE}/conversations", params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("conversations", [])
            out.extend(batch)
            cursor = data.get("next_cursor")
            if not cursor or not batch:
                break
    return out[:limit]


def get_conversation(conversation_id: str) -> Dict[str, Any]:
    """Return the full conversation detail: transcript, tool calls, metadata, analysis."""
    headers = {"xi-api-key": _api_key()}
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(f"{_BASE}/conversations/{conversation_id}", headers=headers)
        resp.raise_for_status()
        return resp.json()


# ----------------------------------------------------------------- normalisers

def conversation_direction(summary_or_detail: Dict[str, Any]) -> str:
    """Best-effort 'inbound' | 'outbound' | 'unknown' from a conversation payload."""
    d = summary_or_detail
    # ElevenLabs exposes it under a few possible keys depending on API version
    for key in ("direction", "call_direction"):
        v = d.get(key)
        if isinstance(v, str) and v:
            return v.lower()
    meta = d.get("metadata") or {}
    phone = meta.get("phone_call") or {}
    v = phone.get("direction")
    if isinstance(v, str) and v:
        return v.lower()
    return "unknown"


def extract_transcript(detail: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalise the transcript to a list of {role, message, tool_calls, tool_results, time}."""
    turns = detail.get("transcript") or []
    out: List[Dict[str, Any]] = []
    for t in turns:
        out.append({
            "role": t.get("role", "unknown"),
            "message": (t.get("message") or "").strip(),
            "time_in_call_secs": t.get("time_in_call_secs"),
            "tool_calls": t.get("tool_calls") or t.get("tool_requests") or [],
            "tool_results": t.get("tool_results") or [],
        })
    return out


def extract_tool_calls(detail: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten all tool calls across the transcript: {tool_name, params, result, is_error}."""
    calls: List[Dict[str, Any]] = []
    for t in detail.get("transcript") or []:
        reqs = t.get("tool_calls") or t.get("tool_requests") or []
        results = {r.get("tool_name") or r.get("requested_tool_name"): r
                   for r in (t.get("tool_results") or [])}
        for r in reqs:
            name = r.get("tool_name") or r.get("requested_tool_name") or "unknown"
            res = results.get(name, {})
            calls.append({
                "tool_name": name,
                "params": r.get("params_as_json") or r.get("tool_details", {}).get("parameters") or r.get("params") or {},
                "result": res.get("result_value") or res.get("tool_results") or res.get("result"),
                "is_error": bool(res.get("is_error")) or bool(res.get("tool_has_been_called") is False),
            })
    return calls


def post_call_summary(detail: Dict[str, Any]) -> Optional[str]:
    analysis = detail.get("analysis") or {}
    s = analysis.get("transcript_summary")
    return s if isinstance(s, str) and s.strip() else None


def avg_latency_secs(detail: Dict[str, Any]) -> Optional[float]:
    meta = detail.get("metadata") or {}
    # ElevenLabs reports a list of per-turn latencies (ms) under metadata
    lat = meta.get("charging", {}).get("llm_latency_ms_list") or meta.get("latency_ms_list")
    if isinstance(lat, list) and lat:
        return sum(lat) / len(lat) / 1000.0
    # fallback: average tool latency if present
    tools_lat = []
    for t in detail.get("transcript") or []:
        for r in (t.get("tool_results") or []):
            ms = r.get("tool_latency_secs")
            if isinstance(ms, (int, float)):
                tools_lat.append(ms)
    if tools_lat:
        return sum(tools_lat) / len(tools_lat)
    return None
