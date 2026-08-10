"""Pydantic models for Ferrite knowledge graph entities."""

import uuid
from datetime import datetime
from typing import Literal, Optional, Protocol

from pydantic import BaseModel, Field


class FactBase(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    predicate: str
    statement: str
    functional: bool
    certainty: Literal["stated", "inferred", "speculative"] = "stated"
    epistemic_state: Literal["active", "contradicted", "superseded"] = "active"
    assertion_source: Literal["user", "tool_result", "model"] = "user"
    valid_at: datetime
    valid_at_inferred: bool = True
    invalid_at: Optional[datetime] = None
    recorded_at: datetime = Field(default_factory=datetime.utcnow)
    namespace: Literal["shared", "personal", "e2e-test"] = "shared"


class Entity(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: Literal["entity", "concept"] = "entity"
    name: str
    summary: Optional[str] = None


class Episode(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    content_type: str
    source: dict
    namespace: str = "shared"
    recorded_at: datetime = Field(default_factory=datetime.utcnow)
    source_file: Optional[str] = None  # relative path in file repo (e.g., "arxiv/2401.12345.txt")


class Observation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    episode_id: str
    fact_id: str


class Alias(BaseModel):
    norm: str


class StoreRequest(BaseModel):
    content: str
    content_type: str = "text"
    source: dict = Field(default_factory=dict)
    namespace: str = "shared"


class StoreResponse(BaseModel):
    episode_id: str
    status: str = "queued"


class SearchResult(BaseModel):
    id: str
    statement: str
    certainty: float = 0.0
    certainty_label: str = "stated"
    source: str = ""
    valid_at: str = ""
    pending_ingestion: bool = False


class SearchResponse(BaseModel):
    results: list[SearchResult]


class HealthResponse(BaseModel):
    neo4j: str
    redis: str
    queue_depth: int


# --- Issue 25: Typed extraction results ---

class ExtractedEntity(BaseModel):
    name: str
    type: Literal["entity", "concept"] = "entity"
    summary: str = ""


class ExtractedFact(BaseModel):
    subject: str
    predicate: str
    object: str
    object_type: Literal["entity", "literal"] = "entity"
    certainty: Literal["stated", "inferred", "speculative"] = "stated"
    assertion_source: Literal["user", "tool_result", "model"] = "model"
    valid_at: Optional[str] = None
    negation: bool = False


class ExtractionResult(BaseModel):
    entities: list[ExtractedEntity] = Field(default_factory=list)
    facts: list[ExtractedFact] = Field(default_factory=list)


# --- Issue 28: Typed key info ---

class KeyInfo(BaseModel):
    key_id: str
    agent_name: str
    scopes: list[str] = Field(default_factory=lambda: ["read", "write"])
    namespaces: list[str] = Field(default_factory=lambda: ["shared"])


# --- Issue 29: Embedder protocol ---

class Embedder(Protocol):
    """Protocol for embedder implementations (e.g. OllamaEmbedder)."""

    def embed(self, text: str) -> Optional[list[float]]:
        ...


# --- Issue 30: Circuit breaker state model ---

class CircuitBreakerState(BaseModel):
    state: str
    failure_count: int
    success_count: int
    failure_threshold: int
    cooldown_seconds: float
    half_open_calls: int
    last_failure_time: Optional[float] = None
