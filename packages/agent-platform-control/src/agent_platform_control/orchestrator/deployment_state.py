"""Shared deployment terminality logic.

Single source of truth for "given a deployment's item counts, what is its
aggregate state". Used by both the worker (clone + cloud-init paths) and the
cancel endpoint so the two cannot drift apart (they did: PR-review #81 — the
worker's copy ignored cancelled items and left cancelled deployments stuck in
``running`` forever).
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..db.models import Deployment


def recompute_deployment_state(dep: Deployment) -> None:
    """Set ``dep.state`` from its item counts.

    Terminal once every item is accounted for — succeeded, failed, or
    cancelled. ``cancelled`` is itself terminal: once an admin has cancelled a
    deployment it is never relabelled — not back to ``running`` while in-flight
    items drain, and not to a ``*_failed`` label when those items resolve. The
    per-item counts still record what actually happened.
    """
    if dep.state == "cancelled":
        dep.updated_at = datetime.now(UTC)
        return
    accounted = dep.succeeded_count + dep.failed_count + dep.cancelled_count
    if accounted >= dep.requested_count:
        if dep.failed_count == 0 and dep.cancelled_count == 0:
            dep.state = "completed"
        elif dep.succeeded_count == 0 and dep.failed_count == 0:
            # only cancellations (no clone outcomes) → cancelled
            dep.state = "cancelled"
        elif dep.succeeded_count == 0:
            dep.state = "failed"
        else:
            dep.state = "partially_failed"
    else:
        dep.state = "running"
    dep.updated_at = datetime.now(UTC)
