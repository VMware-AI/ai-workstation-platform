"""Core protocol: AgentBackend + AgentLaunchSpec + UserContext.

All adapters (qcoder / Goose / Claude Code) implement AgentBackend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class UserContext:
    """Caller identity + workspace, fed into adapter.launch_spec."""

    user_id: str
    workspace: Path
    api_key: str
    llm_gateway_url: str
    model: str | None = None  # optional override; adapter chooses default if None


@dataclass(frozen=True)
class AgentLaunchSpec:
    """What to actually exec. Immutable — adapter returns one per call.

    ``stdin_data``: when set, the runner writes it to the child's stdin
    instead of placing it on the command line. Adapters use this to feed
    the prompt to CLIs that read it from stdin, keeping it off ``argv``
    (where ``ps`` / ``/proc`` would expose it and a long prompt could hit
    ``ARG_MAX``). ``None`` means the child gets no stdin payload.
    """

    name: str
    binary: str
    args: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None
    timeout_s: int = 300
    stdin_data: str | None = None


@runtime_checkable
class AgentBackend(Protocol):
    """Minimal contract every agent adapter satisfies."""

    @property
    def name(self) -> str:
        """Adapter name, e.g. 'qcoder' / 'goose' / 'claude-code'."""
        ...

    @property
    def default_model(self) -> str:
        """Model name to pass when UserContext.model is None."""
        ...

    def launch_spec(self, ctx: UserContext, prompt: str) -> AgentLaunchSpec:
        """Build the launch spec for one agent invocation."""
        ...

    def health_check(self, ctx: UserContext) -> tuple[bool, str]:
        """(ok, message). False ok = degraded; message is user-readable."""
        ...
