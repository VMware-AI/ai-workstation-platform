"""Agent Platform agent adapter (C20).

Multi-agent abstraction: qcoder / Goose / Claude Code (and future) speak through
the same Protocol; user-facing `agent` CLI dispatches.
"""

from .protocol import AgentBackend, AgentLaunchSpec, UserContext
from .registry import REGISTRY, get_agent, list_agents, register_agent
from .telemetry import TelemetryEvent, TelemetryShim

__version__ = "0.1.0"
__all__ = [
    "REGISTRY",
    "AgentBackend",
    "AgentLaunchSpec",
    "TelemetryEvent",
    "TelemetryShim",
    "UserContext",
    "get_agent",
    "list_agents",
    "register_agent",
]
