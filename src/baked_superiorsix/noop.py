"""Shared no-op response for scaffolded endpoints."""

from typing import Any


def noop() -> dict[str, Any]:
    """Placeholder response until domain logic is implemented."""
    return {"noop": True}
