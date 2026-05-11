# Ria — Evaluation Framework

A framework to judge the quality of Ria's calls across the four dimensions in the
assignment. It is **implemented** (not just designed): `eval/` contains a Gemini-backed
LLM-judge + deterministic checks, a Postgres store, and a Streamlit dashboard.

---

## 1. What we evaluate

Calls are pulled live from the ElevenLabs Conversational AI API (`/v1/convai/conversations`),
which already stores every inbound and outbound call with transcript, tool calls + results,
latency metrics, and the post-call summary. We do **not** mirror calls into our own DB — only
the **evaluation results** are persisted (in Postgres). Re-validating a call adds a new row;
the dashboard shows the latest per call.

Each call is scored on **23 checks** grouped into 4 dimensions. Every check returns
`{passed: bool, score: 0-5 | null, na: bool, reasoning: str}` — `score` is only used for
*graded* checks; `na` excludes the check from scoring when it doesn't apply (e.g. a
follow-up check on a call with no follow-ups).

---

## 2. The four dimensions

### A. Conversation Quality — *LLM-judged on the transcript*
| Check | Kind | Critical | What it verifies |
|---|---|---|---|
| `greeting_warm` | pass/fail | | Warm, on-brand opener; identifies as Ria from BlueStone |
| `discovery_flow_followed` | 0-5 | | Walked Occasion → Recipient → Metal → Budget (skipping only what the customer volunteered) |
| `one_question_at_a_time` | pass/fail | ✓ | Never bundled multiple discovery questions in one turn |
| `confirmed_before_search` | pass/fail | | Read back the details and got confirmation before searching |
| `handled_vague_input` | pass/fail | | Asked clarifying Qs instead of searching blindly when the customer was vague |
| `handled_followups` | pass/fail | | "Cheaper" / "white gold instead" / "earrings instead" handled by re-searching with carried-over context |
| `no_repeat_recommendations` | pass/fail | | Didn't re-recommend the same pieces across turns |
| `offered_alternatives_on_empty` | pass/fail | | Offered alternatives instead of dead-ending on a 0-result search |
| `tone_natural` | 0-5 | | Conversational, acknowledged answers before the next question, not robotic |

### B. Tool Correctness — *LLM + deterministic*
| Check | Kind | Critical | Source | What it verifies |
|---|---|---|---|---|
| `no_raw_json_or_urls_spoken` | pass/fail | ✓ | regex on agent turns | No JSON, raw URLs, or bare numeric product IDs read aloud |
| `right_tool_right_time` | 0-5 | | LLM | search only after enough info; details/similar reference a real `design_id`; store lookup only when asked; whatsapp only after agreement |
| `tool_params_valid` | pass/fail | | tool log | `budget_max` numeric, `design_id` present where required, `caller_phone` E.164 |
| `tool_failure_handled` | pass/fail | | LLM | Graceful spoken fallback on a tool error/empty result — no "Error 500", no dead air |
| `whatsapp_sent_when_agreed` | pass/fail | | tool log + transcript | If the customer agreed to WhatsApp, `send_to_whatsapp` was actually invoked and succeeded |

### C. Business Outcome — *LLM + deterministic*
| Check | Kind | Critical | Source | What it verifies |
|---|---|---|---|---|
| `recommendation_made` | pass/fail | ✓ | LLM | Call produced ≥1 concrete recommendation (named pieces with prices) |
| `recommendation_delivered` | pass/fail | | tool log + transcript | Cards sent to WhatsApp **or** the customer explicitly declined |
| `needs_captured` | pass/fail | | LLM | Customer's occasion/recipient/metal/budget were correctly understood and reflected in the search |
| `summary_accurate` | 0-5 | | LLM | ElevenLabs' post-call summary faithfully reflects the conversation — no hallucinations, no key omissions |
| `call_ended_cleanly` | pass/fail | | LLM | Wrapped up warmly and used `end_call`; didn't hang mid-topic or run on aimlessly |

### D. Voice Quality — *mostly deterministic from metadata; LLM only for transcript artifacts*
| Check | Kind | Critical | Source | What it verifies |
|---|---|---|---|---|
| `latency_ok` | pass/fail | | metadata | Avg turn/tool latency ≤ 3 s |
| `no_truncated_turns` | pass/fail | ✓ | transcript regex | No agent turn cut off mid-sentence |
| `no_double_greeting` | pass/fail | | transcript regex | Didn't repeat the greeting / loop |
| `turn_taking_smooth` | 0-5 | | LLM | No awkward gaps / talking over the customer; reasonable turn count for the content |

> Voice quality is genuinely best assessed from the **audio** (ElevenLabs stores the recording),
> but for an automated pass we use transcript-shape signals (truncation, double greeting) plus the
> latency metadata, with one LLM heuristic for overall flow. A human spot-check of the audio is the
> recommended supplement — that's where this framework is intentionally weakest.

---

## 3. Scoring & pass criteria

- **Per check** → a fraction in `[0,1]`: graded checks use `score/5`; pass/fail checks use `1.0` if passed else `0.0`. `n/a` checks are dropped.
- **Per dimension** → weighted mean of its checks' fractions × 100 (weights are in `eval/rubric.py`). A dimension **also fails outright** if any of its `critical` checks fail, regardless of score.
- **Pass thresholds:** Conversation ≥ 80, Tool ≥ 80, Business ≥ 80, Voice ≥ 75.
- **Overall pass** ⇔ all four dimensions pass.
- **Overall score** = mean of the four dimension scores (for ranking/at-a-glance only — the pass/fail is what matters).

Rationale for the metric choice: a single blended score hides regressions (a great conversation
with raw JSON spoken aloud shouldn't "pass"), so we gate on **per-dimension thresholds + critical
checks** rather than one number. Graded 0-5 checks capture "how well" for the subjective things
(flow, tone, summary fidelity); strict pass/fail for the things that should never happen
(bundled questions, raw JSON, truncated turns, no recommendation).

---

## 4. How it's implemented

```
eval/
  rubric.py          # the 23 checks, weights, critical flags, thresholds, scoring fn — single source of truth
  elevenlabs_api.py  # list_conversations(), get_conversation() + normalisers (transcript, tool calls, summary, latency, direction)
  validator.py       # deterministic checks (regex/log-based) + Gemini judge (JSON-mode) → merged → dimension scores → overall pass
  store.py           # SQLAlchemy 'evaluations' table; save_evaluation(), latest_by_conversation(), get_evaluation()
  dashboard.py       # Streamlit: list view (filter, multi-select, batch validate) + detail view (per-check results, transcript, tool calls)
docs/EVAL_FRAMEWORK.md   # this document
```

- **LLM judge:** Gemini (`gemini-2.5-flash` by default, override via `EVAL_JUDGE_MODEL`), called with
  `response_mime_type="application/json"` and temperature 0, given the rubric for the LLM checks plus the
  formatted transcript + tool log + post-call summary. It returns one object per check.
- **Deterministic checks** never trust the LLM: raw-JSON/URL/ID detection (regex on agent turns), `tool_params_valid`
  and `whatsapp_sent_when_agreed`/`recommendation_delivered` (from the tool-call log + simple transcript heuristics),
  `latency_ok` (metadata), `no_truncated_turns` / `no_double_greeting` (transcript shape).
- **Persistence:** one `evaluations` row per validation run — `conversation_id`, `direction`, `validated_at`, `judge_model`,
  `overall_passed`, `overall_score`, the four `dim_*` scores, `dim_passed` (JSONB), and `results` (JSONB — every check with
  passed/score/na/reasoning), plus a `transcript_snapshot` for reproducibility.

### Running it
```bash
# env: ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID, DATABASE_URL (Postgres), GEMINI_API_KEY
uv sync --extra eval
uv run --extra eval streamlit run eval/dashboard.py
```
Dashboard: **list view** shows every call (inbound + outbound) with its eval status/score; tick rows → **Validate selected**;
or open a call → **Validate / re-validate** and see every check with its reasoning, plus the transcript and tool-call timeline.

---

## 5. What "good" looks like (acceptance bar for a release)

- ≥ 90% of a sample of recent calls **pass overall**.
- **Zero** failures on the critical checks (`one_question_at_a_time`, `no_raw_json_or_urls_spoken`,
  `recommendation_made`, `no_truncated_turns`) across the sample.
- Median `summary_accurate` ≥ 4/5; median `tone_natural` ≥ 4/5.
- Of calls where the customer engaged through discovery, ≥ 80% have `recommendation_delivered`.

---

## 6. Limitations / next steps

- **Voice quality** is approximated from transcript shape + latency metadata; a proper version would run the audio
  through an ASR-confidence / VAD-overlap analysis (barge-ins, talk-over, dead air) — ElevenLabs stores the recording, so this is feasible.
- The "customer agreed to WhatsApp" / "declined" detection is heuristic (keyword-based); the LLM checks back this up, but a
  dedicated intent classifier would be more robust.
- LLM-judge variance: mitigated with temperature 0 + a strict rubric + JSON mode, but for high-stakes use you'd run the judge
  2-3× and take the majority, or calibrate against a human-labelled set.
- No automatic regression alerting yet — the dashboard is on-demand. A nightly job that validates the day's calls and posts a
  summary (pass rate, critical failures) would close that loop.
