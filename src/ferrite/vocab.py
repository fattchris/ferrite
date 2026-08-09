"""Controlled predicate vocabulary with functional flags."""


PREDICATE_VOCAB: dict[str, dict] = {
    "works_at": {"functional": True, "description": "Entity works at an organization"},
    "version_is": {"functional": True, "description": "Entity has a specific version"},
    "runs_on": {"functional": True, "description": "Entity runs on a platform"},
    "located_in": {"functional": True, "description": "Entity is located in a place"},
    "married_to": {"functional": True, "description": "Entity is married to another entity"},
    "ceo_of": {"functional": True, "description": "Entity is CEO of an organization"},
    "capital_of": {"functional": True, "description": "Entity is the capital of a country"},
    "author_of": {"functional": False, "description": "Entity authored a work"},
    "uses": {"functional": False, "description": "Entity uses a tool or technology"},
    "depends_on": {"functional": False, "description": "Entity depends on another entity"},
    "related_to": {"functional": False, "description": "Loose association between entities"},
    "employs": {"functional": False, "description": "Organization employs a person"},
    "founded_by": {"functional": False, "description": "Organization founded by entity"},
    "member_of": {"functional": False, "description": "Entity is a member of a group"},
    "parent_of": {"functional": False, "description": "Entity is parent of another entity"},
    "child_of": {"functional": False, "description": "Entity is child of another entity"},
    "part_of": {"functional": False, "description": "Entity is part of a larger entity"},
    "contains": {"functional": False, "description": "Entity contains another entity"},
    "manages": {"functional": False, "description": "Entity manages another entity"},
    "owns": {"functional": False, "description": "Entity owns another entity"},
    "created": {"functional": False, "description": "Entity created a work or artifact"},
    "manufactured_by": {"functional": False, "description": "Product manufactured by entity"},
    "published_on": {"functional": True, "description": "Work published on a date"},
    "born_on": {"functional": True, "description": "Entity born on a date"},
    "died_on": {"functional": True, "description": "Entity died on a date"},
    "height_is": {"functional": True, "description": "Entity has a specific height"},
    "weight_is": {"functional": True, "description": "Entity has a specific weight"},
    "color_is": {"functional": True, "description": "Entity has a specific color"},
    "language_is": {"functional": False, "description": "Work written in a language"},
    "genre_is": {"functional": False, "description": "Work belongs to a genre"},
    "directed_by": {"functional": True, "description": "Work directed by entity"},
    "starring": {"functional": False, "description": "Work stars entity"},
    "released_on": {"functional": True, "description": "Work released on a date"},
    "headquartered_in": {"functional": True, "description": "Org headquartered in a place"},
    "serves": {"functional": False, "description": "Entity serves another entity"},
    "competes_with": {"functional": False, "description": "Entity competes with another"},
    "partnered_with": {"functional": False, "description": "Entity partnered with another"},
    "acquired_by": {"functional": True, "description": "Entity acquired by another"},
    "replaced_by": {"functional": True, "description": "Entity replaced by another"},
    "named": {"functional": True, "description": "Entity has a formal name"},

    # --- Infrastructure & operational predicates ---
    "has_ip": {"functional": True, "description": "Entity has an IP address"},
    "runs_model": {"functional": True, "description": "Node runs a specific model"},
    "deployed_on": {"functional": True, "description": "Model deployed on a node/cluster"},
    "has_gpu": {"functional": True, "description": "Node has a specific GPU type"},
    "has_throughput": {"functional": True, "description": "Model has specific throughput"},
    "has_version": {"functional": True, "description": "Entity has a specific software version"},
    "configured_with": {"functional": False, "description": "Entity configured with a setting"},
    "head_node_of": {"functional": True, "description": "Node is head of a cluster"},
    "part_of_cluster": {"functional": False, "description": "Node is part of a cluster"},
    "has_role": {"functional": True, "description": "Person has a role at organization"},
    "founded_in": {"functional": True, "description": "Organization founded in year/date"},
    "located_at": {"functional": True, "description": "Entity located at address"},
    "has_port": {"functional": True, "description": "Service runs on port"},
    "connects_to": {"functional": False, "description": "Entity connects to another"},
    "uses_framework": {"functional": False, "description": "Entity uses a framework"},
    "rejected": {"functional": False, "description": "Experiment/feature was rejected"},
    "has_regression": {"functional": True, "description": "Experiment has regression percentage"},
    "parent_brand_of": {"functional": False, "description": "Brand is parent of another"},
    "has_memory": {"functional": True, "description": "Device has specific memory amount"},

    # --- Memory & preference predicates (for Hermes memory provider) ---
    "prefers": {"functional": False, "description": "Entity prefers a setting, format, or behavior"},
    "has_preference": {"functional": False, "description": "Entity has a specific preference"},
    "has_timezone": {"functional": True, "description": "Entity uses a specific timezone"},
    "has_profile": {"functional": True, "description": "Person has a specific profile name"},
    "has_role_at": {"functional": True, "description": "Person has a role at organization"},
    "builds": {"functional": False, "description": "Entity builds a project or product"},
    "monitors": {"functional": False, "description": "Entity monitors a system or service"},
    "hosted_on": {"functional": True, "description": "Service hosted on a specific machine"},
    "auth_method": {"functional": True, "description": "Entity uses a specific auth method"},
    "has_constraint": {"functional": False, "description": "Entity has a specific constraint"},
    "prohibited": {"functional": False, "description": "Action is prohibited in context"},
}


def get_predicate(pred_id: str) -> dict:
    """Get predicate metadata. Raises ValueError if unknown."""
    if pred_id not in PREDICATE_VOCAB:
        raise ValueError(f"Unknown predicate: {pred_id}")
    return PREDICATE_VOCAB[pred_id]


def is_functional(pred_id: str) -> bool:
    """Check if a predicate is functional. Raises ValueError if unknown."""
    return get_predicate(pred_id)["functional"]


def is_valid_predicate(pred_id: str) -> bool:
    """Check if a predicate exists in the vocabulary."""
    return pred_id in PREDICATE_VOCAB


def list_predicates() -> list[str]:
    """List all predicate IDs."""
    return list(PREDICATE_VOCAB.keys())
