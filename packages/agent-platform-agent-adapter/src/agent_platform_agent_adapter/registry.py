"""Adapter registry — name → AgentBackend."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .protocol import AgentBackend


REGISTRY: dict[str, AgentBackend] = {}


def register_agent(agent: AgentBackend) -> None:
    """Idempotent: re-register overrides (useful for tests / hot reload)."""
    REGISTRY[agent.name] = agent


def get_agent(name: str) -> AgentBackend:
    if name not in REGISTRY:
        raise KeyError(f"agent {name!r} not registered. Available: {sorted(REGISTRY)}")
    return REGISTRY[name]


def list_agents() -> list[str]:
    return sorted(REGISTRY)
