# Ria — Write‑up: decisions, trade‑offs, edge cases

## Architecture decisions

**ElevenLabs Agents platform for the conversation, FastAPI only for tools.**
The hard parts of a voice agent — barge‑in/turn‑taking, ASR, TTS, the audio WebSocket, an LLM loop with tool calling, the post‑call analysis — are exactly what the ElevenLabs platform does well. Building that on raw Twilio media streams would be weeks of work for a worse result. So the agent (voice, system prompt, LLM, `end_call`, dynamic variables, the 5 tools) lives in ElevenLabs, and our backend is just a thin, stateless‑ish HTTP service that the agent calls: hit BlueStone, shape a voice‑friendly response, send WhatsApp, find a store, place an outbound call. Twilio is connected to the agent via ElevenLabs' "import phone number", so inbound audio streams straight to the agent — our backend isn't in the audio path at all.

**Layered backend with constructor DI.** `api → features → services → repositories → entities/shared`, wired in one container (`app/api/container.py`). It's a small app, but the seams make it easy to test a service in isolation and to swap implementations (e.g. Redis → Postgres for sessions later) without touching the routes. Failures are contained at the boundary: a tool route never returns a 500 to ElevenLabs if it can help it — it returns a `{"say": "...graceful sentence...", "data": {...}}` so Ria says something sensible instead of going silent.

**State: Redis for the live call, ElevenLabs for the record, Postgres for evals.**
In‑call context (occasion, recipient, metal, budget, the current search results so `send_to_whatsapp` can page through them) is small, ephemeral, and read/written on the hot path — Redis, keyed by the ElevenLabs conversation id. The authoritative record of a call (transcript, tool calls, latency, summary) is already kept by ElevenLabs; mirroring it into our DB would be a cache with no upside for this use case, so the eval dashboard reads it on demand. Postgres holds only the thing that's genuinely ours — evaluation results — one row per validation run, with every check's pass/score/reasoning in JSONB and a transcript snapshot for reproducibility.

**`send_to_whatsapp` as its own tool, not a side‑effect.** Earlier the search/details tools auto‑sent cards. The assignment asks for `send_to_whatsapp` to be triggered *during* the call, and it's better UX anyway: Ria narrates the top 3, asks "interested in any of these? I'll WhatsApp the link", and only then sends — and can page through the rest ("a few more designs?"). The backend returns up to 10 slim results (`id, name, price`) so Ria has the ids for the rest of the picks; she tracks what she's already sent in her own conversation context (no server‑side "sent" bookkeeping needed).

**Eval framework: hybrid judge, not LLM‑only.** Subjective things (did she follow the discovery flow? was the tone natural? is the post‑call summary accurate?) are graded by a Gemini judge with a strict rubric, JSON mode, temperature 0. Things that must never happen and are cheap to check deterministically (raw JSON/URLs/IDs spoken, truncated turns, double greeting, latency, "was `send_to_whatsapp` actually called when the customer agreed") are checked in Python and never trusted to the LLM. Scoring gates on **per‑dimension thresholds + critical checks** rather than one blended number, so a great conversation that leaked raw JSON still fails.

**Why Streamlit for the dashboard.** It's the fastest way to a real, clickable UI in pure Python — list view with filters and multi‑select, a detail view with the transcript and per‑check reasoning, and buttons that trigger validation. Deployed as a second Railway service from the same repo (`Dockerfile.dashboard`, `RAILWAY_DOCKERFILE_PATH`), sharing the project's Postgres.

## Trade‑offs between models and design

- **TTS model:** Eleven Flash v2.5 (~75 ms) over Turbo v2.5 — for a phone call, latency beats the marginal quality bump.
- **Agent LLM:** a "flash/mini/haiku"‑tier model is the right call — Ria does structured discovery + reliable tool calling, not deep reasoning, so the latency/cost of a frontier model isn't worth it. Gemini‑Flash‑class is the sweet spot; GPT‑4o‑mini is the safe fallback if tool‑calling reliability ever wobbles.
- **Eval judge:** `gemini-2.5-flash` — cheap, fast, good enough at rubric‑style judging with JSON mode + temp 0. Trade‑off: LLM‑judge variance. Mitigated with a strict rubric and zero temperature; for high‑stakes use you'd run the judge 2–3× and take the majority, or calibrate against a human‑labelled set.
- **Slim vs full tool responses:** returning all 10 full product dicts to the agent is unnecessary context; returning only `id/name/price` for the list (full dicts stay in Redis for the WhatsApp cards) keeps the LLM context tight.
- **Budget as a range tag, no client‑side filtering:** the BlueStone API *does* filter by `rs <from> to <to>` correctly — *if it's the only tag*. So we fold metal/occasion/stone into the query text and send only the budget tag, and trust the API. No client‑side price filtering — it'd be a band‑aid over a working API once you use it right.
- **No Postgres mirror of calls:** keeps the system simpler (no sync job, no staleness). Cost: the dashboard makes API calls to ElevenLabs on each refresh. Fine at this scale; you'd add a mirror only if you needed offline access or queries over tens of thousands of calls.
- **Batch validation on a small thread pool (4):** parallel enough to be useful, bounded so it stays under the Gemini RPM limit and the DB pool. `validate()` is sync I/O, so threads (not asyncio) without a rewrite.

## Edge cases handled

- **BlueStone 403s from cloud IPs** — the catalogue intermittently rate‑limits cloud egress IPs. Mitigated with browser‑like headers and a 3‑attempt exponential‑backoff retry on connection errors and 403/429/5xx; if all retries fail, Ria says "I ran into a problem searching the catalogue — could you repeat your preferences?" instead of crashing.
- **Empty search results** — Ria offers alternatives (broaden the budget, different metal/style) rather than dead‑ending.
- **The budget‑tag quirk** — described above; verified against the live API.
- **Inbound vs outbound caller number** — on inbound, `system__caller_id` is the customer; on outbound it's the agent line, so `/voice/outbound` injects the dialed number as the `outbound_customer_phone` dynamic variable, and the system prompt picks whichever applies. Either way Ria confirms the number before the first WhatsApp send and uses a different one if the customer gives it.
- **WhatsApp's 24‑hour window** — freeform messages (our cards) only deliver within 24 h of the customer's last inbound WhatsApp message; in the sandbox the `join` resets that window. The code already supports approved Content Templates (which have no window) — set `WHATSAPP_TEMPLATE_SID` once a WhatsApp Business sender is onboarded and it switches over with no code change.
- **Phone‑number normalisation** — caller numbers are normalised to `whatsapp:+<E.164>` before sending (handles missing `+`, an existing `whatsapp:` prefix, stray spaces/dashes from the outbound panel).
- **Store lookup by place name** — a customer can say "Koramangala" instead of a pincode; `StoreService` resolves it via `api.postalpincode.in`, then queries the BlueStone store locator; "no store nearby" → Ria offers the online links instead.
- **Double greeting / call running on** — the system prompt has a hard "greet only once" rule (if the customer says "hi" back, acknowledge briefly and move to discovery, don't re‑introduce), and Ria uses the `end_call` tool to wrap up when the conversation is complete. The eval framework's deterministic `no_double_greeting` and `call_ended_cleanly` checks catch regressions.
- **One question at a time** — promoted to a "CRITICAL RULE" in the prompt with wrong/right examples; the original Step 4 (which itself bundled metal + stone) was split. Enforced as a *critical* check in the eval rubric.
- **ElevenLabs quota exhaustion** — observed during development: when conversational‑AI minutes run out, calls drop right after the greeting with "this request exceeds your quota limit". Not a code bug; documented so it isn't mistaken for one.
- **Tool latency / dead air** — every tool has `pre_tool_speech: force`, and the prompt gives Ria filler lines ("let me pull that up for you…") so there's no silence while a tool runs.
- **Webhook signature** — the post‑call webhook HMAC‑verifies the ElevenLabs signature when `ELEVENLABS_WEBHOOK_SECRET` is set, with a 5‑minute timestamp‑skew window to reject replays; when no secret is set it logs a warning and accepts (useful for a demo).
- **Idempotent logging / config validation** — logging setup is guarded against duplicate handlers; required env vars are validated at startup with a clear `ConfigurationError`.

## What I'd do with more time

- **`find_nearest_store` → also WhatsApp the store** as a card (address + map link + click‑to‑call), not just speak it.
- **Voice‑quality eval from the audio**, not the transcript shape — run the ElevenLabs recording through VAD/overlap analysis (barge‑ins, talk‑over, dead air) and ASR confidence; that's where the eval framework is intentionally weakest today.
- **Durable conversation store (Postgres)** alongside Redis — survive restarts, enable analytics across calls, and let the eval dashboard work even if ElevenLabs is slow/down.
- **Approved WhatsApp Content Templates + a real WhatsApp Business sender** so cards deliver outside the 24‑hour window (the code path already exists behind `WHATSAPP_TEMPLATE_SID`).
- **Gold‑coins tool** — there's a BlueStone gold‑coin API; add a `browse_gold_coins` tool for gifting flows.
- **Nightly eval job** — auto‑validate the day's calls and post a summary (pass rate, critical failures) to Slack; right now the dashboard is on‑demand only.
- **Judge robustness** — multi‑sample the LLM judge and take the majority; calibrate the rubric against a small human‑labelled set; add an inter‑annotator agreement check.
- **Retry/proxy for BlueStone** — the cloud‑IP 403s are occasional; a small request proxy or a more aggressive retry budget (with a circuit breaker) would smooth them out.
- **Indian phone number for production** — currently a US Twilio number; a production deployment with an Indian business entity would use a Twilio India regulatory bundle or an Indian CPaaS (Plivo/Exotel) via SIP into ElevenLabs.
- **Tests** — unit tests for the services (especially the budget‑tag composition and the WhatsApp number normalisation) and a contract test against the BlueStone API shapes.
