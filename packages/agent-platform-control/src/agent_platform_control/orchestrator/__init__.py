"""VM provisioning orchestrator.

Two pieces:
  - Provisioner Protocol (protocol.py) — abstraction over vCenter / vcsim / fake
  - Worker (worker.py) — asyncio task that drains pending DeploymentItems

The API layer (api/deployments.py) writes rows; the worker reads them. Process
crashes only lose in-flight clone progress, not state — DB is the truth.
"""

from .protocol import CloneResult, CloneSpec, Provisioner
from .worker import DeploymentWorker

__all__ = ["CloneResult", "CloneSpec", "DeploymentWorker", "Provisioner"]
