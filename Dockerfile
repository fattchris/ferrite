FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first (better layer caching)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code
COPY src/ ./src/
COPY scripts/ ./scripts/

EXPOSE 8000 8001

CMD ["uv", "run", "uvicorn", "ferrite.api:app", "--host", "0.0.0.0", "--port", "8001"]
