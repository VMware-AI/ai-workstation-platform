"""C5 agent-platform-llm-gateway — LiteLLM proxy wrapper.

Exposes a thin Typer CLI (`agent-platform-llm-gateway start`) that:

* validates the operator-supplied config against the platform's
  minimum schema (so a typo in production fails fast, not later when
  the first request hits a missing key)
* defers actual proxy serving to LiteLLM's own runtime
"""

from .config import (
    GatewayConfigError,
    load_config,
    validate_config,
)

__all__ = ["GatewayConfigError", "load_config", "validate_config"]
