"""Errors raised by the memory layer."""


class MemoryModelError(Exception):
    """Base class for memory layer errors (not named MemoryError: that is a builtin)."""


class NotFound(MemoryModelError):
    """A referenced memory record does not exist."""


class InvalidTransition(MemoryModelError):
    """The requested status change is not an allowed transition."""


class ActorNotAllowed(MemoryModelError):
    """The actor is not permitted to make this change (invariant 3, section 9 C8)."""


class InvariantViolation(MemoryModelError):
    """The change would break an invariant from section 3 of the specification."""
