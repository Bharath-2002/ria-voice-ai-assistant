# Ria — BlueStone Voice Calling Assistant

Ria is a voice AI jewellery consultant for BlueStone, built on the **ElevenLabs** Agents platform with a **FastAPI** tool backend. She handles inbound (and outbound) phone calls over **Twilio**, runs a natural discovery conversation (occasion → recipient → metal → budget, one question at a time), searches the **BlueStone catalogue** in real time, recommends pieces conversationally, sends product cards to **WhatsApp** during the call, finds nearby BlueStone stores (and can text the address + map link), produces an LLM-summarised post‑call record that's **injected back into the customer's next call**, and ends the call herself. A separate **Streamlit eval dashboard** scores every call against a 4‑dimension rubric (Gemini LLM judge + deterministic checks) and persists the results in Postgres.

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
- **Searches the catalogue** via custom tools and **sends the top 3 picks straight to the customer's WhatsApp** ("take a look, tell me which one you like") — no long product narration on the call.
- **Handles follow‑ups** — "something cheaper", "white gold instead", "earrings instead", "something like that one", "send me a few more" — by re‑searching with the previous context carried over and paging through the result set without repeating.
- **Knows what the customer already has on their phone.** Every WhatsApp send is tracked, and a per‑call LLM summary is persisted in Postgres along with the rich list of products that landed on the customer's WhatsApp. On the *next* call, that history (summary + the design IDs of past sent products) is injected into the agent — so Ria can open with *"Welcome back! Last time I sent over The Tatva Mangalsutra and The Floral Ambrosia Necklace — did either catch your eye?"* and respond to *"resend that mangalsutra"* with a direct `send_to_whatsapp(design_id=…)`, no catalogue search.
- **Finds the nearest BlueStone store** from a pincode or place name and texts all nearby store addresses + map links on request.
- **Ends the call itself** when the conversation is complete (ElevenLabs `end_call` system tool).
- **Post‑call**: ElevenLabs fires a webhook; the backend runs a background Gemini summary, upserts the customer + conversation rows in Postgres, and finalises the Redis session.

### Tools (FastAPI endpoints, registered as ElevenLabs server tools)

| Tool | Endpoint | Purpose |
|---|---|---|
| `search_products` | `POST /tools/search_products` | Search the BlueStone catalogue. Folds metal/occasion/stone into the query text and sends the budget as `rs <from> to <to>` (the only tag that filters — see [the budget quirk](#the-budget-quirk)). Returns up to 10 slim results plus the IDs of the top 3 to send. |
| `get_product_details` | `POST /tools/get_product_details` | Full details for one product by `design_id` (metal, weight, carats, collection, price, link). |
| `find_similar` | `POST /tools/find_similar` | Designs similar to a product the customer liked. |
| `send_to_whatsapp` | `POST /tools/send_to_whatsapp` | Send product card(s) (photo, price, link) for given `design_ids` to the customer's WhatsApp. Tracks what was sent in the session for later memory. |
| `find_nearest_store` | `POST /tools/find_nearest_store` | Nearest BlueStone store(s) for a `location` — accepts a 6‑digit pincode **or** a place name (resolved via `api.postalpincode.in`). Can also text every nearby store's address + map link to the customer's WhatsApp on request. |

Other backend routes: `POST /voice/inbound` & `POST /voice/status` (Twilio webhooks — only used if Twilio is *not* directly imported into ElevenLabs), `POST /voice/outbound` (`{"to_number": "+91…"}` → ElevenLabs places an outbound call via Twilio, with prior‑call memory injected), `POST /elevenlabs/initiation` (inbound — injects prior‑call memory as a dynamic variable), `POST /elevenlabs/post-call` (transcript + Gemini summary persisted), `GET /health`.

---

## Architecture

```
                    Twilio (PSTN)  ──┐
                                     │  audio (mulaw 8 kHz, WebSocket)
                    ElevenLabs Agent ◄┘  ── "Ria": voice, turn‑taking, system prompt, LLM, end_call,
                          │                  dynamic variables ({{previous_conversations}} etc.)
                          │  tool calls (HTTPS)            ▲
                          ▼                                │ post‑call webhook (transcript, summary)
   ┌──────────────────────────────────────────────────────┴───────────────┐
   │  FastAPI backend  (Railway: service "ria-app")                        │
   │    api/routes ─► features/ConversationFeature ─► services ─► repos    │
   │    BlueStoneService (catalogue, retry + browser headers + proxy hook) │
   │    StoreService (store locator + India pincode lookup)                │
   │    WhatsAppService (Twilio WhatsApp — product cards + store details)  │
   │    SessionService ─► RedisSessionRepository  ──►  Redis (Railway)     │
   │    MemoryService (Gemini summary)                                     │
   │           ▲                ─► MemoryRepository ─► Postgres            │
   │           │                       (customers, conversations)          │
   │           │                                                           │
   │    on /elevenlabs/initiation  &  /voice/outbound:                     │
   │    MemoryService.recent_for_prompt(phone) — last 3 summaries +        │
   │    each call's WhatsApp-sent products (id+name+price) injected into   │
   │    {{previous_conversations}} so Ria recognises the returning caller. │
   └──────────────────────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────────────────┐
   │  Eval dashboard  (Railway: service "ria-eval-dashboard", Streamlit)   │
   │    pulls calls from ElevenLabs API  ──►  validator (Gemini judge +    │
   │    deterministic checks)  ──►  Postgres (Railway): evaluations table  │
   └──────────────────────────────────────────────────────────────────────┘
```

**Layering** (`app/`): `api → features → services → repositories → entities/shared`. Dependencies only point downward; everything is wired in `app/api/container.py` via constructor injection. `eval/` is a standalone module that talks to the ElevenLabs API and Postgres.

### State — Redis, Postgres, ElevenLabs (who owns what)

Three stores, each with a job:

- **Redis** — per‑call **working state**. Every tool call from ElevenLabs is an independent stateless HTTP request to the backend, and we may run multiple replicas, so anything shared *between* tool calls of one conversation needs an external store. Redis holds the customer's captured preferences, the current search's full catalogue results (so `send_to_whatsapp` can build cards and page through "a few more" while the agent passes only IDs), and the running list of products actually sent to WhatsApp. Keyed by the ElevenLabs conversation id (every tool carries `system__conversation_id`), with a TTL so finished calls auto‑expire.
- **Postgres** — durable, cross‑call **memory** (customers + conversations, schema-managed by Alembic) **and** evaluation results. The post‑call webhook spawns a background Gemini summary call → upserts `customers` (find‑or‑create by phone, COALESCEs the name if newly captured) → upserts `conversations` by ElevenLabs conversation id (idempotent for webhook retries). On the customer's next call, `MemoryService.recent_for_prompt(phone)` joins those two tables and builds a `previous_conversations` string that's injected into the agent's prompt.
- **ElevenLabs** — owns the **authoritative call record** (transcript, tool calls, latency, its own `transcript_summary`). We don't mirror that; the eval dashboard reads it on demand. The LLM's turn‑to‑turn conversational memory ("the second one", "something cheaper") lives in the ElevenLabs side too — Redis isn't duplicating that, it's the *backend's* shared working store.

### The budget quirk

The BlueStone search API's budget tag (`rs <from> to <to>`) **only filters when it is the only tag** — add any metal/occasion/stone tag alongside it and the price range is silently ignored (verified against the live API). So `BlueStoneService` composes the `search_query` text from `<metal> <occasion> <stone> <item>` and sends **only** the budget tag. "Under 50000" → `rs 0 to 50000`; "20k–50k" → `rs 20000 to 50000`; "above 2 lakhs" → `rs 200000 to <high ceiling>`; no preference → no budget tag.

---

## Tooling: uv + Alembic

- **[uv](https://docs.astral.sh/uv/)** is the package manager and runner. The project's deps and lockfile live in `pyproject.toml` + `uv.lock`; `uv sync` creates `.venv` and installs the locked set, `uv run <cmd>` runs anything inside that venv. The Docker image uses `uv sync --frozen --no-dev` so production gets the exact locked deps, no source‑of‑truth drift between dev and prod. Optional deps live in `[project.optional-dependencies] eval = [...]` — installed via `uv sync --extra eval` for the dashboard service.
- **[Alembic](https://alembic.sqlalchemy.org/)** manages the Postgres schema for `customers` and `conversations`. `migrations/versions/0001_init_memory.py` is the initial schema. The Dockerfile runs `alembic upgrade head` *before* uvicorn starts on every deploy (and gracefully skips it when `DATABASE_URL` isn't set, so local dev without Postgres still works). `migrations/env.py` uses an `include_object` callback so Alembic never touches the eval framework's `evaluations` table (owned by `eval/store.py` in the same Postgres). To add a new schema change later: edit the SQLAlchemy models in `app/repositories/memory_repository.py`, run `uv run alembic revision -m "describe change"` (or `--autogenerate`), commit, deploy — the next container roll applies it.

---

## Run it locally

**Prerequisites:** [uv](https://docs.astral.sh/uv/getting-started/installation/), Docker (for Redis + Postgres), a Twilio account (WhatsApp sandbox enabled), an ElevenLabs account, and a Google Gemini API key (for the post‑call summariser + the eval judge).

```bash
# 1. install deps (uv creates .venv)
uv sync                     # voice backend + cross-call memory
uv sync --extra eval        # + eval dashboard deps (streamlit, pandas)

# 2. configure
cp .env.example .env        # then fill in the values

# 3. local infra
docker compose up -d        # Redis on :6379, Postgres on :5433

# 4. database schema
uv run alembic upgrade head # creates customers + conversations tables

# 5a. run the voice tool backend
uv run ria                  # == uvicorn app.api.app:app  (http://localhost:8000)

# 5b. run the eval dashboard (separate terminal)
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
| `ELEVENLABS_WEBHOOK_SECRET` | backend | optional — when set, post‑call webhooks are HMAC‑verified. Leave empty to skip verification |
| `BLUESTONE_BASE_URL` | backend | default `https://www.bluestone.com` |
| `BLUESTONE_PROXY` | backend | optional HTTP proxy URL (e.g. a Webshare rotating endpoint) — BlueStone's catalogue intermittently 403s requests from cloud IPs; setting a proxy routes catalogue calls through it. Empty = unchanged |
| `REDIS_URL` | backend | default `redis://localhost:6379/0` |
| `REDIS_TTL_SECONDS` | backend | per‑call session TTL; default 86400 (24 h) |
| `DATABASE_URL` | backend, eval | Postgres. The backend uses it for the cross‑call memory tables (Alembic‑managed); the eval dashboard uses the same Postgres for `evaluations` (created lazily by `eval/store.py`). Skip on `ria-app` to disable cross‑call memory |
| `GEMINI_API_KEY` | backend, eval | required for the post‑call summariser and the eval judge |
| `EVAL_JUDGE_MODEL` | backend, eval | default `gemini-2.5-flash`; used by both the eval judge and the post‑call summariser |
| `RIA_APP_URL` | eval dashboard | base URL of the voice backend for the outbound‑call panel (default = the Railway URL) |
| `EVAL_PARALLELISM` | eval dashboard | batch‑validation thread pool size (default 4) |

---

## ElevenLabs agent configuration

The agent "Ria" is configured in ElevenLabs (mostly via the API — see the commit history): a warm female voice on the low‑latency TTS model, a strong system prompt (persona, one‑question‑at‑a‑time rule, returning‑customer recognition, discovery flow, catalogue vocabulary, the recommendation/WhatsApp/store flow, name capture, end‑call behaviour), the 5 server tools above, the `end_call` system tool, and dynamic variables — `system__caller_id` (inbound), `outbound_customer_phone` (injected by `/voice/outbound`), `previous_conversations` (injected by both initiation paths from the cross‑call memory) — so tool calls receive the right phone number and Ria recognises returning callers. Twilio is connected to the agent (ElevenLabs "import phone number") so inbound audio streams straight to the agent.

The full system prompt is in [`docs/SYSTEM_PROMPT.md`](docs/SYSTEM_PROMPT.md).

---

## Evaluation framework

Implemented in `eval/` with a Streamlit dashboard — see **[`docs/EVAL_FRAMEWORK.md`](docs/EVAL_FRAMEWORK.md)** for the full design (the 23 checks, weights, scoring, pass thresholds). In short: every call is scored on four dimensions — **Conversation Quality, Tool Correctness, Business Outcome, Voice Quality** — using a Gemini LLM judge for the subjective checks and Python for the deterministic ones (raw‑JSON/URL detection, tool‑log analysis, latency, truncation). Results persist in Postgres; the dashboard lists every inbound/outbound call, lets you validate one or many in parallel, and shows every check with the judge's reasoning. Run: `uv run --extra eval streamlit run eval/dashboard.py`.

---

## Deployment (Railway)

Four services in one Railway project, all from this repo:

| Service | Build | Start | Notes |
|---|---|---|---|
| `ria-app` | `Dockerfile` | `alembic upgrade head` → `uvicorn app.api.app:app` | the voice tool backend; gets a public HTTPS URL used as the ElevenLabs tool/webhook URL. Migrations run on every deploy (idempotent) |
| `ria-eval-dashboard` | `Dockerfile.dashboard` (selected via `RAILWAY_DOCKERFILE_PATH`) | `streamlit run eval/dashboard.py` | the eval UI |
| `Redis` | Railway plugin | — | in‑call session state; `REDIS_URL` referenced into `ria-app` |
| `Postgres` | Railway plugin | — | `customers`, `conversations` (Alembic), `evaluations`; `DATABASE_URL` referenced into both `ria-app` and `ria-eval-dashboard` |

Set the env vars from the table above on each service (Railway's `${{Service.VAR}}` syntax wires Redis/Postgres URLs automatically). After `ria-app` is deployed, set its URL as the tool/webhook URL in ElevenLabs and the status callback in Twilio.

---

## Evaluator quick‑start

1. **Call +1 701 575 1233** and talk to Ria. Try: *"I'm looking for a gold necklace for my wife's anniversary, around fifty thousand."* Then *"send the second one to my WhatsApp"*, *"any similar designs?"*, *"is there a store near Koramangala?"*
2. For the **WhatsApp** cards to actually arrive, first message `join buried-audience` to **+1 415 523 8886** (WhatsApp's 24‑hour window — re‑join if it's been a while).
3. **Call again from the same number a few minutes later** — Ria's first message should reference your previous call by name, e.g. *"Welcome back! Last time we were looking at gold necklaces — did anything catch your eye?"*. Try *"resend the Tatva mangalsutra"* — she'll text it directly without re‑searching, because the design IDs of previously sent products are in her prompt.
4. After the call, open the **eval dashboard**, hit *Refresh calls*, find your call, click *Validate* (or select several and *Validate selected*), and inspect the per‑dimension scores and per‑check reasoning. The sidebar's *Trigger outbound call* panel will ring a number you enter (must be a Twilio‑verified number while the account is on trial).

See **[`docs/WRITEUP.md`](docs/WRITEUP.md)** for architecture decisions, trade‑offs, edge cases handled, and what I'd do with more time.
