FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first (better layer caching)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code and install the project itself
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY eval/ ./eval/
RUN uv sync --frozen --no-dev

# Default env — override via docker-compose
ENV NEO4J_URI=bolt://localhost:7687 \
    NEO4J_USER=neo4j \
    NEO4J_PASSWORD=ferrite123 \
    REDIS_URL=redis://localhost:6379 \
    LLM_MODEL=glm-5.2 \
    LOG_LEVEL=INFO

EXPOSE 8000 8001

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

CMD ["uv", "run", "uvicorn", "ferrite.api:app", "--host", "0.0.0.0", "--port", "8001"]
