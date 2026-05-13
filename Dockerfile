FROM python:3.11-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency files first (layer cache optimisation)
COPY pyproject.toml uv.lock ./

# Install dependencies into the system Python (no venv needed in container)
RUN uv sync --frozen --no-dev --no-editable

COPY . .

EXPOSE 8000

# Run DB migrations first (idempotent — Alembic skips already-applied revisions),
# then start the API. If DATABASE_URL isn't set we skip migrations so local dev
# without Postgres still works.
CMD ["/bin/sh", "-c", "if [ -n \"$DATABASE_URL\" ]; then uv run alembic upgrade head; else echo 'no DATABASE_URL — skipping migrations'; fi && uv run uvicorn app.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
