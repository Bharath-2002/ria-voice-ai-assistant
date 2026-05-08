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
