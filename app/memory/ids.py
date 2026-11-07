"""Prefixed text identifiers for memory records (specification section 9)."""

import uuid


ID_PREFIXES: dict[str, str] = {
    "incident": "inc",
    "observation": "obs",
    "hypothesis": "hyp",
    "diagnostic_action": "act",
    "tool_result": "tr",
    "evidence": "ev",
    "memory_relation": "rel",
    "knowledge": "kn",
    "memory_event": "evt",
}


def new_id(entity_type: str) -> str:
    """Return ``<prefix>_<uuid4 hex>`` for an entity type."""
    try:
        prefix = ID_PREFIXES[entity_type]
    except KeyError:
        raise ValueError(f"unknown entity type: {entity_type!r}") from None
    return f"{prefix}_{uuid.uuid4().hex}"


def has_prefix(entity_type: str, value: object) -> bool:
    """Return True when ``value`` looks like an id of ``entity_type``."""
    prefix = ID_PREFIXES.get(entity_type)
    return isinstance(value, str) and prefix is not None and value.startswith(prefix + "_")
