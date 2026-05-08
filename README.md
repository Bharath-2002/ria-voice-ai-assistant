# BlueStone Voice Assistant

A production-grade voice AI jewelry consultant — Ria — that receives calls via Twilio, converses naturally using ElevenLabs, searches the BlueStone catalog in real time, and sends product cards to WhatsApp during the call.

## Quick Start

### Prerequisites
- Python 3.9+
- Docker & Docker Compose
- Twilio account (with WhatsApp sandbox enabled)
- ElevenLabs account

### Setup

```bash
# 1. Install dependencies
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env and fill in your credentials

# 3. Start Redis and PostgreSQL
docker-compose up -d

# 4. Run the server
python main.py
```

### Test the tool endpoint

```bash
curl -X POST http://localhost:8000/tools/search_products \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "test-001",
    "search_query": "diamond earrings",
    "metal_preference": "gold",
    "budget_max": 50000
  }'
```

## Architecture

```
API Routes (FastAPI)
    ↓
Features (ConversationFeature)
    ↓
Services (BlueStoneService, SessionService)
    ↓
Repositories (RedisSessionRepository)
    ↓
Entities & Shared (Config, Logging, Exceptions)
```

Dependency flow is always **downward** — layers never import from above.

---

## Evaluator Testing Instructions

### Make a voice call to Ria

Call **+1 701 575 1233** to speak with the BlueStone voice assistant directly.

### Test WhatsApp product cards

1. Open WhatsApp and send a message to **+1 415 523 8886**
2. Send the join code to activate the Twilio sandbox:
   ```
   join buried-audience
   ```
3. Once joined, call **+1 701 575 1233** — Ria will send product cards to your WhatsApp during the conversation.

### Suggested test scenarios

| Scenario | What to say |
|----------|-------------|
| Budget filter | "Show me gold earrings under 30,000 rupees" |
| Occasion-based | "I need a gift for my wife's anniversary" |
| Metal preference | "I prefer platinum, what necklaces do you have?" |
| Product detail | Ask about a specific item Ria recommends |
| Clarification | Give a vague request and see if Ria probes for context |

---

## Production Deployment (Railway)

```bash
# 1. Push repo to GitHub
# 2. Create project on railway.app and connect the repo
# 3. Add Redis plugin → Railway injects REDIS_URL automatically
# 4. Add PostgreSQL plugin → Railway injects DATABASE_URL automatically
# 5. Set remaining env vars in Railway dashboard (copy from .env.example)
# 6. Deploy → Railway generates a public HTTPS URL
# 7. Set that URL as the webhook in Twilio and ElevenLabs dashboards
```
