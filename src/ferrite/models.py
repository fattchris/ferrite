"""Pydantic models for the Ferrite knowledge graph.""""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EntityType(str, Enum):
    entity = "entity"
    concept = "concept"


class Certainty(str, Enum):
    stated = "stated"
    inferred = "inferred"
    speculative = "speculative"


class EpistemicState(str, Enum):
    active = "active"
    contradicted = "contradicted"
    superseded = "superseded"


class AssertionSource(str, Enum):
    user = "user"
    tool_result = "tool_result"
    model = "model"


class Namespace(str, Enum):
    shared = "shared"
    personal = "personal"


class Entity(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: EntityType = EntityType.entity
    name: str
    summary: Optional[str] = None


class Fact(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    predicate: str
    statement: str
    functional: bool = False
    certainty: Certainty = Certainty.stated
    epistemic_state: EpistemicState = EpistemicState.active
    assertion_source: AssertionSource = AssertionSource.user
    valid_at: datetime = Field(default_factory=utc_now)
    valid_at_inferred: bool = True
    invalid_at: Optional[datetime] = None
    recorded_at: datetime = Field(default_factory=utc_now)
    namespace: Namespace = Namespace.shared
    negation: bool = False


class Episode(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    content_type: str = "text/plain"
    source: dict[str, Any]
    namespace: Namespace = Namespace.shared
    created_at: datetime = Field(default_factory=utc_now)


class Alias(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    norm: str


class Observation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    episode_id: str
    observed_at: datetime = Field(default_factory=utc_now)


class ExtractedFact(BaseModel):
    subject: str
    predicate: str
    object: str
    object_type: str = "entity"
    certainty: Certainty = Certainty.stated
    assertion_source: AssertionSource = AssertionSource.model
    valid_at: Optional[datetime] = None
    negation: bool = False


class ExtractedEntity(BaseModel):
    name: str
    type: EntityType = EntityType.entity
    summary: Optional[str] = None


class ExtractionResult(BaseModel):
    entities: list[ExtractedEntity] = []
    facts: list[ExtractedFact] = []
