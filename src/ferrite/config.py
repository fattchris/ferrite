"""Application configuration loaded from environment variables.""""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "password"
    REDIS_URL: str = "redis://localhost:6379/0"
    LLM_API_KEY: str = "test-key"
    LLM_BASE_URL: str = "http://localhost:4000/v1"
    NAMESPACE_DEFAULT: str = "shared"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EXTRACTION_MODEL: str = "gpt-4o-mini"
    EMBEDDING_DIMENSIONS: int = 1536


@lru_cache
def get_settings() -> Settings:
    return Settings()
