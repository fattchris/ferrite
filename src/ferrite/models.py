"""Pydantic models for Ferrite knowledge graph entities."""

import uuid
from datetime import datetime
from typing import Literal, Optional

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
    namespace: Literal["shared", "personal"] = "shared"


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
