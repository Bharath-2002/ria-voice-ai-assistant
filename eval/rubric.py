"""The evaluation rubric — single source of truth for checks, weights, and pass thresholds.

Four dimensions (mirrors the assignment):
  - conversation : did Ria follow the discovery flow, one question at a time, etc.  (LLM-judged)
  - tool         : right tools at the right time, no raw JSON/URLs spoken, failures handled  (LLM + deterministic)
  - business     : recommendation made & delivered, post-call summary accurate  (LLM + deterministic)
  - voice        : latency ok, no truncated turns, smooth turn-taking  (mostly deterministic)

Each check is judged as {passed: bool, score: int|None (0-5), reasoning: str}.
"score" is used for graded checks; pass/fail-only checks leave it None.
A check may return "n/a" (not applicable to this call) — it's then excluded from scoring.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Literal

Kind = Literal["bool", "graded"]      # bool = pass/fail only; graded = 0-5 score
Source = Literal["llm", "deterministic"]
Dimension = Literal["conversation", "tool", "business", "voice"]


@dataclass(frozen=True)
class Check:
    name: str
    dimension: Dimension
    kind: Kind
    source: Source
    weight: float            # relative weight within its dimension
    critical: bool = False   # if a critical check fails, the whole dimension fails
    description: str = ""     # shown to the LLM judge (for llm checks) and in the dashboard


CHECKS: List[Check] = [
    # ---------------------------------------------------------------- conversation
    Check("greeting_warm", "conversation", "bool", "llm", 1.0,
          description="Opened with a warm, on-brand greeting and identified herself as Ria from BlueStone."),
    Check("discovery_flow_followed", "conversation", "graded", "llm", 2.0,
          description="Walked the customer through Occasion -> Recipient -> Metal -> Budget, skipping only what the customer volunteered. Score 5 = all relevant steps covered in a natural order; 0 = jumped straight to searching with no discovery."),
    Check("one_question_at_a_time", "conversation", "bool", "llm", 2.0, critical=True,
          description="Never bundled multiple discovery questions into one turn (e.g. 'What's the occasion, who's it for, and your budget?' is a FAIL). Asking one question per turn is required."),
    Check("confirmed_before_search", "conversation", "bool", "llm", 1.5,
          description="Read back the gathered details and got the customer's confirmation before calling search_products."),
    Check("handled_vague_input", "conversation", "bool", "llm", 1.0,
          description="When the customer was vague ('show me something nice'), asked clarifying questions instead of searching blindly. Return n/a if the customer was never vague."),
    Check("handled_followups", "conversation", "bool", "llm", 1.5,
          description="Handled follow-up requests like 'something cheaper' / 'white gold instead' / 'earrings instead' by re-searching with the previous context carried over. Return n/a if there were no follow-up requests."),
    Check("no_repeat_recommendations", "conversation", "bool", "llm", 1.0,
          description="Did not re-recommend the same products across turns. Return n/a if only one search was performed."),
    Check("offered_alternatives_on_empty", "conversation", "bool", "llm", 1.0,
          description="When a search returned no results, offered alternatives (broaden budget, different metal, etc.) instead of dead-ending. Return n/a if every search returned results."),
    Check("tone_natural", "conversation", "graded", "llm", 1.5,
          description="Conversational and human, not robotic; acknowledged each answer before asking the next thing; no awkward phrasing. Score 5 = warm and natural throughout; 0 = stilted/scripted."),

    # ----------------------------------------------------------------------- tool
    Check("no_raw_json_or_urls_spoken", "tool", "bool", "deterministic", 2.0, critical=True,
          description="No raw JSON, raw URLs, or bare numeric product IDs were read aloud to the customer."),
    Check("right_tool_right_time", "tool", "graded", "llm", 2.0,
          description="Called the right tool at the right moment: search_products only after enough info was gathered; get_product_details / find_similar referenced a product from earlier results; find_nearest_store only when the customer asked about stores; send_to_whatsapp only after the customer agreed. Score 5 = all tool calls well-timed and appropriate; 0 = wrong/random tool usage."),
    Check("tool_params_valid", "tool", "bool", "deterministic", 1.0,
          description="Tool parameters were sensible: budget_max numeric, design_id present where required, caller_phone in E.164 form for whatsapp."),
    Check("tool_failure_handled", "tool", "bool", "llm", 1.5,
          description="When a tool returned an error or empty result, Ria gave a graceful spoken fallback (no 'Error 500', no dead silence, no exposing the failure). Return n/a if no tool failed."),
    Check("whatsapp_sent_when_agreed", "tool", "bool", "deterministic", 1.5,
          description="If the customer agreed to receive WhatsApp cards, send_to_whatsapp was actually invoked and reported success. Return n/a if the customer never agreed to WhatsApp."),

    # ------------------------------------------------------------------- business
    Check("recommendation_made", "business", "bool", "llm", 2.0, critical=True,
          description="The call produced at least one concrete product recommendation (named pieces with prices)."),
    Check("recommendation_delivered", "business", "bool", "deterministic", 1.5,
          description="The recommendation was delivered to WhatsApp (send_to_whatsapp succeeded) OR the customer explicitly declined delivery."),
    Check("needs_captured", "business", "bool", "llm", 1.0,
          description="The customer's stated needs (occasion, recipient, metal, budget) were correctly understood and reflected in the search Ria performed."),
    Check("summary_accurate", "business", "graded", "llm", 1.5,
          description="The post-call summary faithfully reflects what actually happened in the conversation — no hallucinated details, no important omissions. Score 5 = fully accurate and useful; 0 = wrong or misleading. Return n/a if no post-call summary is available."),
    Check("call_ended_cleanly", "business", "bool", "llm", 1.0,
          description="Ria wrapped up warmly and ended the call appropriately (used the end_call tool) — did not hang up mid-topic or let the call run on aimlessly. Return n/a if the call was clearly cut off by a technical issue."),

    # ---------------------------------------------------------------------- voice
    Check("latency_ok", "voice", "bool", "deterministic", 1.5,
          description="Average tool/turn latency was within an acceptable range for a phone conversation."),
    Check("no_truncated_turns", "voice", "bool", "deterministic", 1.5, critical=True,
          description="No agent turn was cut off mid-sentence in the transcript."),
    Check("no_double_greeting", "voice", "bool", "deterministic", 1.0,
          description="Ria did not repeat the greeting or loop back to the start."),
    Check("turn_taking_smooth", "voice", "graded", "llm", 1.0,
          description="Turn-taking felt natural — no obviously awkward gaps, no talking over the customer, reasonable number of turns for the content. Score 5 = smooth; 0 = choppy/awkward."),
]

# Pass thresholds (per dimension) and overall.
PASS_THRESHOLDS: Dict[str, int] = {
    "conversation": 80,
    "tool": 80,
    "business": 80,
    "voice": 75,
}
# A dimension also fails outright if any of its `critical` checks fail (regardless of score).
# The call "passes overall" iff every dimension passes.

LLM_JUDGE_MODEL_DEFAULT = "gemini-2.5-flash"

# Convenience views
CHECKS_BY_DIMENSION: Dict[str, List[Check]] = {}
for _c in CHECKS:
    CHECKS_BY_DIMENSION.setdefault(_c.dimension, []).append(_c)

LLM_CHECKS: List[Check] = [c for c in CHECKS if c.source == "llm"]
DETERMINISTIC_CHECKS: List[Check] = [c for c in CHECKS if c.source == "deterministic"]
DIMENSIONS: List[str] = ["conversation", "tool", "business", "voice"]


def score_dimension(dimension: str, results: Dict[str, dict]) -> tuple[int, bool]:
    """Return (score 0-100, passed) for one dimension given per-check results.

    `results` maps check_name -> {"passed": bool, "score": int|None, "na": bool}.
    Graded checks contribute score/5; bool checks contribute 1.0 if passed else 0.
    n/a checks are dropped. If a critical check failed, passed=False regardless.
    """
    checks = CHECKS_BY_DIMENSION[dimension]
    total_w = 0.0
    got_w = 0.0
    critical_failed = False
    for c in checks:
        r = results.get(c.name)
        if r is None or r.get("na"):
            continue
        passed = bool(r.get("passed"))
        if c.critical and not passed:
            critical_failed = True
        if c.kind == "graded":
            sc = r.get("score")
            frac = (sc / 5.0) if isinstance(sc, (int, float)) else (1.0 if passed else 0.0)
        else:
            frac = 1.0 if passed else 0.0
        total_w += c.weight
        got_w += c.weight * frac
    if total_w == 0:
        return (100, True)  # nothing applicable -> vacuously pass
    pct = round(100 * got_w / total_w)
    passed = (pct >= PASS_THRESHOLDS[dimension]) and not critical_failed
    return (pct, passed)
