"""Allowed status transitions and actor permissions (specification sections 2, 3, 9).

Pure functions without database access. Conditions that need stored
evidence (invariants 1, 4, 5 and C11) are checked by the repository.
"""

from app.memory.errors import ActorNotAllowed, InvalidTransition
from app.memory.models import ACTORS


# (entity_type, field) -> {old status: allowed new statuses}
TRANSITIONS: dict[tuple[str, str], dict[str, frozenset[str]]] = {
    ("incident", "status"): {
        "open": frozenset({"investigating"}),
        "investigating": frozenset({"resolved", "unresolved"}),
        "resolved": frozenset({"investigating"}),
        "unresolved": frozenset({"investigating"}),
    },
    ("hypothesis", "status"): {
        "proposed": frozenset({"testing"}),
        "testing": frozenset({"supported", "refuted", "inconclusive"}),
        "supported": frozenset({"testing"}),
        "refuted": frozenset({"testing"}),
        "inconclusive": frozenset({"testing"}),
    },
    ("diagnostic_action", "status"): {
        "planned": frozenset({"running", "skipped"}),
        "running": frozenset({"succeeded", "failed"}),
    },
    ("diagnostic_action", "approval"): {
        "pending": frozenset({"approved", "rejected"}),
    },
    ("memory_relation", "status"): {
        "asserted": frozenset({"verified", "refuted"}),
    },
    ("knowledge", "status"): {
        "unverified": frozenset({"verified", "failed", "deprecated"}),
        "verified": frozenset({"failed", "deprecated"}),
        "failed": frozenset({"verified", "deprecated"}),
    },
}

HYPOTHESIS_FINAL = frozenset({"supported", "refuted", "inconclusive"})
INCIDENT_CLOSED = frozenset({"resolved", "unresolved"})

# Who may create each kind of record (C8): the LLM only creates records, never
# opens incidents (D1), stores tool output, or writes knowledge.
CREATE_ACTORS: dict[str, frozenset[str]] = {
    "incident": frozenset({"user"}),
    "observation": frozenset(ACTORS),
    "hypothesis": frozenset(ACTORS),
    "diagnostic_action": frozenset(ACTORS),
    "tool_result": frozenset({"rule"}),
    "evidence": frozenset(ACTORS),
    "memory_relation": frozenset(ACTORS),
    "knowledge": frozenset({"rule", "user"}),
}

# The only status change the LLM may make (C8).
LLM_TRANSITIONS = frozenset({("hypothesis", "status", "proposed", "testing")})


def check_actor(actor: object) -> str:
    if actor not in ACTORS:
        raise ValueError(f"actor must be one of {', '.join(ACTORS)}; got {actor!r}")
    return actor  # type: ignore[return-value]


def check_create(entity_type: str, actor: str) -> None:
    check_actor(actor)
    if actor not in CREATE_ACTORS[entity_type]:
        raise ActorNotAllowed(f"actor {actor!r} may not create {entity_type} records")


def check_transition(entity_type: str, field: str, old: str, new: str, actor: str) -> None:
    """Raise unless ``actor`` may change ``field`` from ``old`` to ``new``."""
    check_actor(actor)
    allowed = TRANSITIONS[(entity_type, field)].get(old, frozenset())
    if new not in allowed:
        raise InvalidTransition(f"{entity_type}.{field}: {old!r} -> {new!r} is not allowed")
    if actor == "llm" and (entity_type, field, old, new) not in LLM_TRANSITIONS:
        raise ActorNotAllowed(
            f"the LLM may only propose; {entity_type}.{field} {old!r} -> {new!r} "
            "must be set by a rule or the user"
        )
    if (entity_type, field) == ("diagnostic_action", "approval") and actor != "user":
        raise ActorNotAllowed("only the user may approve or reject an action")
