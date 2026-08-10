"""Application settings loaded from environment variables + ferrite.yaml (§15.2)."""

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings


def _load_yaml_config() -> dict:
    """Load ferrite.yaml if it exists (§15.2)."""
    yaml_path = Path(os.environ.get("FERRITE_CONFIG_YAML", "ferrite.yaml"))
    if not yaml_path.exists():
        # Try finding it relative to the package
        package_dir = Path(__file__).parent
        yaml_path = package_dir.parent.parent / "ferrite.yaml"
    if yaml_path.exists():
        with open(yaml_path) as f:
            return yaml.safe_load(f) or {}
    return {}


_yaml_config = _load_yaml_config()


class Settings(BaseSettings):
    # Neo4j (§15.2 database)
    NEO4J_URI: str = _yaml_config.get("database", {}).get("uri", "bolt://localhost:7687")
    NEO4J_USER: str = _yaml_config.get("database", {}).get("username", "neo4j")
    NEO4J_PASSWORD: str = _yaml_config.get("database", {}).get("password", "password")

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # LLM (§15.2 llm — §11.1 extraction model)
    LLM_API_KEY: str = os.environ.get("LLM_API_KEY", "")
    LLM_BASE_URL: str = _yaml_config.get("llm", {}).get(
        "api_base", "http://localhost:4000/v1"
    )
    LLM_MODEL: str = _yaml_config.get("llm", {}).get("model", "glm-5.2")
    LLM_TIMEOUT: int = int(_yaml_config.get("llm", {}).get("timeout", 120))

    # Embeddings (§15.2 embedder — §11.2)
    EMBED_BASE_URL: str = _yaml_config.get("embedder", {}).get(
        "api_base", "http://localhost:11434/v1"
    )
    EMBED_MODEL: str = _yaml_config.get("embedder", {}).get(
        "model", "text-embedding-3-small"
    )
    EMBEDDING_MODEL: str = EMBED_MODEL  # backward compat

    # Server (§15.2 server)
    SERVER_TRANSPORT: str = _yaml_config.get("server", {}).get("transport", "http")
    SERVER_PORT: int = _yaml_config.get("server", {}).get("port", 8001)

    # Circuit breaker (§15.2 circuit_breaker)
    CB_FAILURE_THRESHOLD: int = _yaml_config.get("circuit_breaker", {}).get(
        "failure_threshold", 5
    )
    CB_COOLDOWN_SECONDS: int = _yaml_config.get("circuit_breaker", {}).get(
        "cooldown_seconds", 60
    )
    CB_HALF_OPEN_MAX_CALLS: int = _yaml_config.get("circuit_breaker", {}).get(
        "half_open_max_calls", 3
    )

    # Eval (§15.2 eval)
    EVAL_QUERIES_FILE: str = _yaml_config.get("eval", {}).get(
        "queries_file", "eval/queries.yaml"
    )
    EVAL_RECALL_K: list[int] = _yaml_config.get("eval", {}).get("recall_k", [5, 10])

    # RRF (§15.2 rrf)
    RRF_K: int = _yaml_config.get("rrf", {}).get("k", 60)
    RRF_RECENCY_WEIGHT: float = _yaml_config.get("rrf", {}).get("recency_weight", 0.15)

    # Backup (§15.2 backup)
    BACKUP_SCHEDULE: str = _yaml_config.get("backup", {}).get("schedule", "0 2 * * *")
    BACKUP_RETENTION_DAYS: int = _yaml_config.get("backup", {}).get("retention_days", 30)
    BACKUP_PATH: str = _yaml_config.get("backup", {}).get("path", "/backups")

    # Namespace
    NAMESPACE_DEFAULT: str = "shared"

    # Key store
    KEYS_DB_PATH: str = str(Path.home() / "ferrite" / "data" / "keys.db")

    # Logging (§8 — structured JSON to stderr for Docker capture)
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")

    # Rate limiting (§4.1)
    READ_RATE_LIMIT: int = 100
    WRITE_RATE_LIMIT: int = 20

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
