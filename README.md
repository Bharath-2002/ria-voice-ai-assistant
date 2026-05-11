# Ria — BlueStone Voice Calling Assistant

Ria is a voice AI jewellery consultant for BlueStone, built on the **ElevenLabs** Agents platform with a **FastAPI** tool backend. She handles inbound (and outbound) phone calls over **Twilio**, runs a natural discovery conversation (occasion → recipient → metal → budget), searches the **BlueStone catalogue** in real time, recommends pieces conversationally, sends product cards to **WhatsApp** during the call, finds nearby BlueStone stores, and produces a post‑call summary. A separate **Streamlit eval dashboard** scores every call against a 4‑dimension rubric (LLM judge + deterministic checks) and persists the results in Postgres.

---

## Live demo

| | |
|---|---|
| 📞 **Call Ria** | **+1 701 575 1233** (inbound) |
| 💬 **WhatsApp sandbox** | message `join buried-audience` to **+1 415 523 8886** first, then call — cards arrive on WhatsApp during the call |
| 🌐 **Tool backend** | `https://ria-app-production.up.railway.app` (`/health`, `/tools/*`, `/voice/*`, `/elevenlabs/*`) |
| 📊 **Eval dashboard** | `https://ria-eval-dashboard-production.up.railway.app` — list of all calls, validate one or many, see per‑check scores; sidebar has a "Trigger outbound call" panel (+91 preset) and a light/dark toggle |

> The Twilio account is on a trial, so **outbound** calls only reach numbers verified in the Twilio console. Inbound works for anyone.

---

## What Ria does

- **Greets once**, then asks **one question at a time** through the discovery flow (occasion → recipient → metal → budget), confirms before searching, handles vague input by probing.
- **Searches the catalogue** via custom tools and **narrates the top 3** picks (names + prices, never raw JSON/URLs/IDs).
- **Handles follow‑ups** — "something cheaper", "white gold instead", "earrings instead", "something like that one" — by re‑searching with the previous context carried over, without repeating recommendations.
- **Sends to WhatsApp on request** — "interested in any of these? I'll send the link" → sends the picked piece(s) → "anything else, or a few more designs?" → pages through the rest of the results.
- **Wraps up** by pointing the customer to the online links *or* a nearby BlueStone store, and can **find the nearest store** from a pincode or place name.
- **Ends the call itself** when the conversation is complete (ElevenLabs `end_call` system tool).
- **Post‑call**: ElevenLabs fires a webhook with the transcript + summary, which the backend logs (and HMAC‑verifies when a secret is set).

### Tools (FastAPI endpoints, registered as ElevenLabs server tools)

| Tool | Endpoint | Purpose |
|---|---|---|
| `search_products` | `POST /tools/search_products` | Search the BlueStone catalogue. Folds metal/occasion/stone into the query text and sends the budget as `rs <from> to <to>` (the only tag that filters — see [the budget quirk](#the-budget-quirk)). Returns top‑3 narration + up to 10 slim results. |
| `get_product_details` | `POST /tools/get_product_details` | Full details for one product by `design_id` (metal, weight, carats, collection, price, link). |
| `find_similar` | `POST /tools/find_similar` | Designs similar to a product the customer liked. |
| `send_to_whatsapp` | `POST /tools/send_to_whatsapp` | Send product card(s) (photo, price, link) for given `design_ids` to the customer's WhatsApp. |
| `find_nearest_store` | `POST /tools/find_nearest_store` | Nearest BlueStone store for a `location` — accepts a 6‑digit pincode **or** a place name (resolved via `api.postalpincode.in`). Returns name/address/timings + a Google Maps link. |

Other backend routes: `POST /voice/inbound` & `POST /voice/status` (Twilio webhooks), `POST /voice/outbound` (`{"to_number": "+91…"}` → ElevenLabs places an outbound call via Twilio), `POST /elevenlabs/initiation` & `POST /elevenlabs/post-call` (ElevenLabs webhooks), `GET /health`.

---

## Architecture

```
                    Twilio (PSTN)  ──┐
                                     │  audio (mulaw 8 kHz, WebSocket)
                    ElevenLabs Agent ◄┘  ── "Ria": voice, turn‑taking, system prompt, LLM, end_call
                          │  tool calls (HTTPS)            ▲
                          ▼                                │ post‑call webhook (transcript, summary)
   ┌──────────────────────────────────────────────────────┴───────────────┐
   │  FastAPI backend  (Railway: service "ria-app")                        │
   │    api/routes ─► features/ConversationFeature ─► services ─► repos    │
   │    BlueStoneService (catalogue, retry+browser headers)                │
   │    StoreService (store locator + pincode lookup)                      │
   │    WhatsAppService (Twilio WhatsApp)                                  │
   │    SessionService ─► RedisSessionRepository  ──►  Redis (Railway)     │
   │    VoiceService (TwiML, outbound‑call API, call lifecycle)            │
   └──────────────────────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────────────────┐
   │  Eval dashboard  (Railway: service "ria-eval-dashboard", Streamlit)   │
   │    pulls calls from ElevenLabs API  ──►  validator (Gemini judge +    │
   │    deterministic checks)  ──►  Postgres (Railway): evaluations table  │
   └──────────────────────────────────────────────────────────────────────┘
```

**Layering** (`app/`): `api → features → services → repositories → entities/shared`. Dependencies only point downward; everything is wired in `app/api/container.py` via constructor injection. `eval/` is a standalone module (own optional deps) that talks to the ElevenLabs API and Postgres.

**Memory / state — why Redis:** every tool call from ElevenLabs is an *independent, stateless HTTP request* to the backend — there's no in‑process session. To carry working state *between* tool calls of the same conversation (the customer's stated preferences, and the full catalogue results of the current search so `send_to_whatsapp` can page through them while the agent passes only ids), we need a store that all those requests — across restarts and across horizontally‑scaled replicas — can see. That's **Redis**: low latency, keyed by the ElevenLabs conversation id (`conversation:<id>`, passed to every tool via the `system__conversation_id` dynamic variable), with a TTL so finished calls auto‑expire. The two real consumers: `send_to_whatsapp` (reads the cached product data to build cards) and the **post‑call webhook** (reads the session to assemble a structured post‑call record — captured preferences + recommended products + ElevenLabs' summary — before persistence). Conversation transcripts/summaries themselves are owned by **ElevenLabs** (the eval dashboard reads them on demand — we don't mirror calls). **Postgres** holds only evaluation results. (The LLM's turn‑to‑turn conversational memory — "the second one", "something cheaper" — is ElevenLabs‑side; Redis is the backend's shared working store, not a duplicate of that.)

**Conversation memory across the call** is also reinforced in the system prompt: Ria carries occasion/metal/budget/recipient between turns, doesn't re‑recommend the same pieces, and asks one thing at a time.

### The budget quirk

The BlueStone search API's budget tag (`rs <from> to <to>`) **only filters when it is the only tag** — add any metal/occasion/stone tag alongside it and the price range is silently ignored (verified against the live API). So `BlueStoneService` composes the `search_query` text from `<metal> <occasion> <stone> <item>` and sends **only** the budget tag. "Under 50000" → `rs 0 to 50000`; "20k–50k" → `rs 20000 to 50000`; no preference → no budget tag.

---

## Run it locally

**Prerequisites:** [uv](https://docs.astral.sh/uv/getting-started/installation/), Docker (for Redis + Postgres), a Twilio account (WhatsApp sandbox enabled), an ElevenLabs account, and a Google Gemini API key (for the eval judge).

```bash
# 1. install deps (uv creates .venv)
uv sync                     # voice backend only
uv sync --extra eval        # + eval dashboard deps

# 2. configure
cp .env.example .env        # then fill in the values

# 3. local infra
docker compose up -d        # Redis on :6379, Postgres on :5433

# 4a. run the voice tool backend
uv run ria                  # == uvicorn app.api.app:app  (http://localhost:8000)

# 4b. run the eval dashboard (separate terminal)
uv run --extra eval streamlit run eval/dashboard.py
```

Smoke‑test a tool:
```bash
curl -X POST http://localhost:8000/tools/search_products -H 'Content-Type: application/json' \
  -d '{"search_query":"diamond earrings","metal_preference":"white gold","occasion":"wedding","budget_max":50000}'
```

To take real calls locally you'd expose the backend (e.g. ngrok) and point ElevenLabs/Twilio at it — but the hosted Railway deployment already does this.

### Environment variables

| Var | Used by | Notes |
|---|---|---|
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER` | backend | required |
| `TWILIO_WHATSAPP_NUMBER` | backend | default `whatsapp:+14155238886` (sandbox) |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID` | backend, eval | required; the API key needs the ElevenAgents (convai) write scope for outbound calls |
| `ELEVENLABS_PHONE_NUMBER_ID` | backend | the imported Twilio number's ElevenLabs id (for outbound calls) |
| `ELEVENLABS_WEBHOOK_SECRET` | backend | optional — when set, post‑call webhooks are HMAC‑verified |
| `BLUESTONE_BASE_URL` | backend | default `https://www.bluestone.com` |
| `REDIS_URL` | backend | default `redis://localhost:6379/0` |
| `DATABASE_URL` | eval | Postgres; the eval dashboard auto‑creates the `evaluations` table |
| `GEMINI_API_KEY` | eval | the LLM‑as‑judge (`gemini-2.5-flash`); `EVAL_JUDGE_MODEL` to override |
| `RIA_APP_URL` | eval dashboard | base URL of the voice backend for the outbound‑call panel (default = the Railway URL) |
| `EVAL_PARALLELISM` | eval dashboard | batch‑validation thread pool size (default 4) |

---

## ElevenLabs agent configuration

The agent "Ria" is configured in ElevenLabs (mostly via the API — see the commit history): a warm female voice on the low‑latency TTS model, a strong system prompt (persona, one‑question‑at‑a‑time rule, discovery flow, catalogue vocabulary, the recommendation/WhatsApp/store flow, end‑call behaviour), the 5 server tools above, the `end_call` system tool, and dynamic variables (`system__caller_id` for inbound, `outbound_customer_phone` injected by `/voice/outbound`) so tool calls receive the customer's WhatsApp number. Twilio is connected to the agent (ElevenLabs "import phone number") so inbound audio streams straight to the agent.

The full system prompt is in [`docs/SYSTEM_PROMPT.md`](docs/SYSTEM_PROMPT.md).

---

## Evaluation framework

Implemented in `eval/` with a Streamlit dashboard — see **[`docs/EVAL_FRAMEWORK.md`](docs/EVAL_FRAMEWORK.md)** for the full design (the 23 checks, weights, scoring, pass thresholds). In short: every call is scored on four dimensions — **Conversation Quality, Tool Correctness, Business Outcome, Voice Quality** — using a Gemini LLM judge for the subjective checks and Python for the deterministic ones (raw‑JSON/URL detection, tool‑log analysis, latency, truncation). Results persist in Postgres; the dashboard lists every inbound/outbound call, lets you validate one or many (in parallel), and shows every check with the judge's reasoning. Run: `uv run --extra eval streamlit run eval/dashboard.py`.

---

## Deployment (Railway)

Four services in one Railway project, all from this repo:

| Service | Build | Start | Notes |
|---|---|---|---|
| `ria-app` | `Dockerfile` | `uvicorn app.api.app:app` | the voice tool backend; gets a public HTTPS URL used as the ElevenLabs tool/webhook URL and the Twilio status callback |
| `ria-eval-dashboard` | `Dockerfile.dashboard` (set via `RAILWAY_DOCKERFILE_PATH`) | `streamlit run eval/dashboard.py` | the eval UI |
| `Redis` | Railway plugin | — | in‑call session state; `REDIS_URL` referenced into `ria-app` |
| `Postgres` | Railway plugin | — | `evaluations` table; `DATABASE_URL` referenced into `ria-eval-dashboard` |

Set the env vars from the table above on each service (Railway's `${{Service.VAR}}` syntax wires Redis/Postgres URLs automatically). After `ria-app` is deployed, set its URL as the tool/webhook URL in ElevenLabs and the status callback in Twilio.

---

## Evaluator quick‑start

1. **Call +1 701 575 1233** and talk to Ria. Try: *"I'm looking for a gold necklace for my wife's anniversary, around fifty thousand."* Then *"send the second one to my WhatsApp"*, *"any similar designs?"*, *"is there a store near Koramangala?"*
2. For the **WhatsApp** cards to actually arrive, first message `join buried-audience` to **+1 415 523 8886** (WhatsApp's 24‑hour window — re‑join if it's been a while).
3. After the call, open **the eval dashboard**, hit *Refresh calls*, find your call, click *Validate* (or select several and *Validate selected*), and inspect the per‑dimension scores and per‑check reasoning. The sidebar's *Trigger outbound call* panel will ring a number you enter (must be a Twilio‑verified number while the account is on trial).

See **[`docs/WRITEUP.md`](docs/WRITEUP.md)** for architecture decisions, trade‑offs, edge cases handled, and what I'd do with more time.
