"""Validate a conversation against the rubric: deterministic checks + Gemini LLM judge.

Public entry point: validate(conversation_id) -> dict (the evaluation row payload).
"""

import json
import os
import re
from typing import Any, Dict, List, Optional

from eval import elevenlabs_api as el
from eval.rubric import (
    CHECKS,
    DETERMINISTIC_CHECKS,
    DIMENSIONS,
    LLM_CHECKS,
    LLM_JUDGE_MODEL_DEFAULT,
    score_dimension,
)

_E164_RE = re.compile(r"^\+\d{7,15}$")
_URL_RE = re.compile(r"https?://\S+")
_JSON_RE = re.compile(r"[{\[].*[:,].*[}\]]")          # crude "looks like JSON" detector
_LONG_INT_RE = re.compile(r"(?<!\d)\d{4,}(?!\d)")     # bare 4+ digit number (product id-ish)
_LATENCY_OK_SECS = 3.0


# --------------------------------------------------------------------------- helpers

def _agent_turns(transcript: List[Dict[str, Any]]) -> List[str]:
    return [t["message"] for t in transcript if t["role"] in ("agent", "assistant") and t["message"]]


def _customer_agreed_to_whatsapp(transcript: List[Dict[str, Any]]) -> bool:
    """Heuristic: an agent turn offers WhatsApp, the next customer turn is affirmative."""
    affirm = re.compile(r"\b(yes|yeah|yep|sure|please|ok|okay|go ahead|that works|sounds good)\b", re.I)
    for i, t in enumerate(transcript):
        if t["role"] in ("agent", "assistant") and "whatsapp" in t["message"].lower():
            for j in range(i + 1, min(i + 3, len(transcript))):
                if transcript[j]["role"] in ("user", "customer") and affirm.search(transcript[j]["message"]):
                    return True
    return False


# ----------------------------------------------------------------- deterministic checks

def _run_deterministic(detail: Dict[str, Any], transcript: List[Dict[str, Any]],
                       tool_calls: List[Dict[str, Any]]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    agent_text = "\n".join(_agent_turns(transcript))

    # no_raw_json_or_urls_spoken
    bad = []
    if _URL_RE.search(agent_text):
        bad.append("a URL")
    if _JSON_RE.search(agent_text):
        bad.append("JSON-like text")
    if _LONG_INT_RE.search(re.sub(r"[₹,]", "", agent_text)):
        # allow prices like 102499 only if preceded by ₹ or "rupees"/"rs" — rough heuristic
        for m in _LONG_INT_RE.finditer(re.sub(r"[,]", "", agent_text)):
            ctx = agent_text[max(0, m.start() - 12):m.start()].lower()
            if "₹" not in ctx and "rs" not in ctx and "rupee" not in ctx and "lakh" not in ctx:
                bad.append(f"a bare number ({m.group()})")
                break
    out["no_raw_json_or_urls_spoken"] = {
        "passed": not bad,
        "score": None,
        "na": False,
        "reasoning": "Clean — no raw JSON/URLs/IDs spoken." if not bad else f"Agent spoke {', '.join(bad)}.",
    }

    # tool_params_valid
    issues = []
    for c in tool_calls:
        p = c.get("params") or {}
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except Exception:
                p = {}
        name = c["tool_name"]
        if name in ("get_product_details", "find_similar") and not p.get("design_id"):
            issues.append(f"{name} missing design_id")
        if name == "search_products" and p.get("budget_max") is not None:
            try:
                float(p["budget_max"])
            except (TypeError, ValueError):
                issues.append("budget_max not numeric")
        cp = p.get("caller_phone")
        if name == "send_to_whatsapp" and cp and not _E164_RE.match(str(cp)):
            issues.append(f"caller_phone not E.164 ({cp})")
    out["tool_params_valid"] = {
        "passed": not issues, "score": None, "na": not tool_calls,
        "reasoning": "All tool params look valid." if not issues else "; ".join(issues),
    }

    # whatsapp_sent_when_agreed
    agreed = _customer_agreed_to_whatsapp(transcript)
    sent_ok = any(c["tool_name"] == "send_to_whatsapp" and not c["is_error"] for c in tool_calls)
    out["whatsapp_sent_when_agreed"] = {
        "passed": sent_ok if agreed else True,
        "score": None,
        "na": not agreed,
        "reasoning": ("Customer agreed and send_to_whatsapp succeeded." if (agreed and sent_ok)
                      else "Customer agreed but send_to_whatsapp was not called / failed." if agreed
                      else "Customer never agreed to WhatsApp."),
    }

    # recommendation_delivered
    declined = re.search(r"\b(no|not now|don'?t|skip|that'?s ok|no thanks)\b", " ".join(
        t["message"] for t in transcript if t["role"] in ("user", "customer")), re.I) is not None
    out["recommendation_delivered"] = {
        "passed": bool(sent_ok or (agreed is False and declined)),
        "score": None, "na": False,
        "reasoning": ("Cards delivered via WhatsApp." if sent_ok
                      else "Customer declined delivery." if declined else "Nothing was delivered and the customer didn't decline."),
    }

    # latency_ok
    lat = el.avg_latency_secs(detail)
    out["latency_ok"] = {
        "passed": (lat is None) or (lat <= _LATENCY_OK_SECS),
        "score": None, "na": lat is None,
        "reasoning": (f"Avg latency {lat:.2f}s." if lat is not None else "No latency data."),
    }

    # no_truncated_turns
    truncated = [t["message"] for t in transcript
                 if t["role"] in ("agent", "assistant") and t["message"]
                 and not re.search(r'[.!?…"”\)]\s*$', t["message"]) and len(t["message"]) > 15]
    out["no_truncated_turns"] = {
        "passed": not truncated, "score": None, "na": False,
        "reasoning": "No truncated agent turns." if not truncated
                     else f"{len(truncated)} agent turn(s) appear cut off (e.g. '…{truncated[0][-40:]}').",
    }

    # no_double_greeting
    greetings = [t for t in _agent_turns(transcript) if re.search(r"\bI'?m Ria\b|\bthis is Ria\b", t, re.I)]
    out["no_double_greeting"] = {
        "passed": len(greetings) <= 1, "score": None, "na": False,
        "reasoning": "Single greeting." if len(greetings) <= 1 else f"Greeting repeated {len(greetings)}x.",
    }
    return out


# --------------------------------------------------------------------- Gemini judge

def _build_judge_prompt(transcript: List[Dict[str, Any]], tool_calls: List[Dict[str, Any]],
                        summary: Optional[str]) -> str:
    lines = ["TRANSCRIPT:"]
    for t in transcript:
        who = "RIA" if t["role"] in ("agent", "assistant") else "CUSTOMER" if t["role"] in ("user", "customer") else t["role"].upper()
        if t["message"]:
            lines.append(f"{who}: {t['message']}")
        for tc in t["tool_calls"]:
            nm = tc.get("tool_name") or tc.get("requested_tool_name") or "tool"
            lines.append(f"  [RIA called tool: {nm} with {json.dumps(tc.get('params_as_json') or tc.get('params') or {})[:300]}]")
        for tr in t["tool_results"]:
            nm = tr.get("tool_name") or "tool"
            val = json.dumps(tr.get("result_value") or tr.get("result") or "")[:300]
            lines.append(f"  [tool {nm} returned: {val}{'  (ERROR)' if tr.get('is_error') else ''}]")
    lines.append("")
    lines.append("TOOL CALLS (flattened): " + json.dumps([
        {"tool": c["tool_name"], "params": c["params"], "error": c["is_error"]} for c in tool_calls
    ])[:2000])
    lines.append("")
    lines.append("POST-CALL SUMMARY: " + (summary or "(none available)"))
    return "\n".join(lines)


def _judge_with_gemini(transcript, tool_calls, summary, model: str) -> Dict[str, dict]:
    """Ask Gemini to evaluate the LLM-judged checks. Returns check_name -> {passed, score, na, reasoning}."""
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=api_key)

    check_specs = [{"name": c.name, "kind": c.kind, "what_to_check": c.description} for c in LLM_CHECKS]
    system = (
        "You are a strict, fair QA evaluator for 'Ria', a voice AI jewellery consultant for BlueStone. "
        "You will be given a call transcript (with tool calls/results) and the post-call summary. "
        "Evaluate ONLY the checks listed. For each check return: passed (true/false), "
        "score (integer 0-5 ONLY if the check kind is 'graded', otherwise null), "
        "na (true if the check does not apply to this call — e.g. a follow-up check when there were no follow-ups), "
        "and a one-sentence reasoning grounded in the transcript. Be specific, quote briefly. "
        "Do not invent things that aren't in the transcript."
    )
    prompt = (
        f"{system}\n\nCHECKS TO EVALUATE:\n{json.dumps(check_specs, indent=2)}\n\n"
        f"{_build_judge_prompt(transcript, tool_calls, summary)}\n\n"
        "Return JSON: an object mapping each check name to {\"passed\": bool, \"score\": int|null, \"na\": bool, \"reasoning\": str}."
    )
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0),
    )
    raw = resp.text or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        parsed = json.loads(m.group(0)) if m else {}
    out: Dict[str, dict] = {}
    valid_names = {c.name for c in LLM_CHECKS}
    for name, v in (parsed or {}).items():
        if name not in valid_names or not isinstance(v, dict):
            continue
        out[name] = {
            "passed": bool(v.get("passed")),
            "score": v.get("score") if isinstance(v.get("score"), (int, float)) else None,
            "na": bool(v.get("na")),
            "reasoning": str(v.get("reasoning", ""))[:500],
        }
    # any LLM check the model omitted -> mark unknown (fail-safe: na=False, passed=False)
    for c in LLM_CHECKS:
        out.setdefault(c.name, {"passed": False, "score": None, "na": False,
                                "reasoning": "Judge did not return a result for this check."})
    return out


# ---------------------------------------------------------------------------- main

def validate(conversation_id: str, judge_model: Optional[str] = None) -> Dict[str, Any]:
    """Run the full rubric against one ElevenLabs conversation. Returns the evaluation payload."""
    model = judge_model or os.environ.get("EVAL_JUDGE_MODEL", LLM_JUDGE_MODEL_DEFAULT)
    detail = el.get_conversation(conversation_id)
    transcript = el.extract_transcript(detail)
    tool_calls = el.extract_tool_calls(detail)
    summary = el.post_call_summary(detail)
    direction = el.conversation_direction(detail)
    agent_id = (detail.get("agent_id") or (detail.get("metadata") or {}).get("agent_id") or "")

    det = _run_deterministic(detail, transcript, tool_calls)
    llm = _judge_with_gemini(transcript, tool_calls, summary, model)
    merged: Dict[str, dict] = {**det, **llm}

    # build per-check result list + dimension scores
    results: List[Dict[str, Any]] = []
    for c in CHECKS:
        r = merged.get(c.name, {"passed": False, "score": None, "na": False, "reasoning": "no result"})
        results.append({
            "dimension": c.dimension, "name": c.name, "kind": c.kind, "source": c.source,
            "critical": c.critical, "weight": c.weight, "description": c.description,
            "passed": bool(r.get("passed")), "score": r.get("score"),
            "na": bool(r.get("na")), "reasoning": r.get("reasoning", ""),
        })

    by_name = {r["name"]: r for r in results}
    dim_scores: Dict[str, int] = {}
    dim_pass: Dict[str, bool] = {}
    for d in DIMENSIONS:
        sc, ok = score_dimension(d, by_name)
        dim_scores[d] = sc
        dim_pass[d] = ok
    overall_score = round(sum(dim_scores.values()) / len(DIMENSIONS))
    overall_passed = all(dim_pass.values())

    return {
        "conversation_id": conversation_id,
        "agent_id": agent_id,
        "direction": direction,
        "judge_model": model,
        "overall_passed": overall_passed,
        "overall_score": overall_score,
        "dim_conversation": dim_scores["conversation"],
        "dim_tool": dim_scores["tool"],
        "dim_business": dim_scores["business"],
        "dim_voice": dim_scores["voice"],
        "dim_passed": dim_pass,
        "results": results,
        "transcript_snapshot": transcript,
        "tool_calls_snapshot": tool_calls,
        "post_call_summary": summary,
    }
