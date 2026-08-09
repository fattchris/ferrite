"""Controlled predicate vocabulary with functional flags.""""

from typing import Optional

# ~40 controlled predicates.
# Functional predicates allow only one active value at a time.
VOCAB: dict[str, dict] = {
    # Functional
    "works_at": {"functional": True, "description": "Subject is employed by or operates within Object organization"},
    "version_is": {"functional": True, "description": "Subject has specific version Object"},
    "runs_on": {"functional": True, "description": "Subject executes on Object platform"},
    "ceo_of": {"functional": True, "description": "Subject is CEO of Object organization"},
    "capital_is": {"functional": True, "description": "Subject has capital city Object"},
    "located_in": {"functional": True, "description": "Subject is geographically located in Object"},
    "founded_in": {"functional": True, "description": "Subject was founded in Object year"},
    "died_in": {"functional": True, "description": "Subject died in Object year"},
    "born_in": {"functional": True, "description": "Subject was born in Object year"},
    "state_is": {"functional": True, "description": "Subject has current state Object"},
    "author_is": {"functional": True, "description": "Subject authored by Object"},
    "parent_is": {"functional": True, "description": "Subject has parent Object"},

    # Non-functional
    "uses": {"functional": False, "description": "Subject uses Object"},
    "depends_on": {"functional": False, "description": "Subject depends on Object"},
    "related_to": {"functional": False, "description": "Subject is related to Object"},
    "manages": {"functional": False, "description": "Subject manages Object"},
    "owns": {"functional": False, "description": "Subject owns Object"},
    "funded_by": {"functional": False, "description": "Subject funded by Object"},
    "member_of": {"functional": False, "description": "Subject is member of Object group"},
    "part_of": {"functional": False, "description": "Subject is part of Object"},
    "supports": {"functional": False, "description": "Subject supports Object"},
    "integrates_with": {"functional": False, "description": "Subject integrates with Object"},
    "competes_with": {"functional": False, "description": "Subject competes with Object"},
    "built_by": {"functional": False, "description": "Subject built by Object"},
    "maintained_by": {"functional": False, "description": "Subject maintained by Object"},
    "designed_by": {"functional": False, "description": "Subject designed by Object"},
    "deployed_at": {"functional": False, "description": "Subject deployed at Object location"},
    "hosts": {"functional": False, "description": "Subject hosts Object"},
    "contains": {"functional": False, "description": "Subject contains Object"},
    "produces": {"functional": False, "description": "Subject produces Object"},
    "consumes": {"functional": False, "description": "Subject consumes Object"},
    "replaced_by": {"functional": False, "description": "Subject replaced by Object"},
    "derived_from": {"functional": False, "description": "Subject derived from Object"},
    "specified_by": {"functional": False, "description": "Subject specified by Object"},
    "classified_as": {"functional": False, "description": "Subject classified as Object"},
    "instance_of": {"functional": False, "description": "Subject is instance of Object"},
    "alias_of": {"functional": False, "description": "Subject is alias of Object"},
    "released_on": {"functional": False, "description": "Subject released on Object date"},
    "occurred_on": {"functional": False, "description": "Subject occurred on Object date"},
}


def get_predicate(pred_id: str) -> Optional[dict]:
    return VOCAB.get(pred_id)


def is_functional(pred_id: str) -> bool:
    p = VOCAB.get(pred_id)
    return p["functional"] if p else False


def is_valid_predicate(pred_id: str) -> bool:
    return pred_id in VOCAB
