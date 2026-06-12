"""Audit-trail writer + text sanitizer for destructive operations.

Repurposes the :class:`AuditView` table — previously an unused "read-mirror"
placeholder — as the control plane's own audit log. Destructive operations
(VM destroy today; clone/force-redeploy as they land) record one row each so
"who destroyed VM X, when, and why" is answerable (harness doc 34 H-6/H-7).

Two guarantees:
  * **Sanitize at the boundary** — any text coming from vCenter/NSX is stripped
    of C0/C1 control characters and truncated before it is logged, stored, or
    returned, so a hostile/garbled API string cannot inject into logs or an
    LLM transcript, nor bloat a row (CLAUDE.md "数据消毒").
  * **Atomic with the operation** — the audit row is added to the *same*
    session/transaction as the state change it records, so they commit together
    (no orphan audit, no orphan destroy). Row construction is guarded so a bad
    value degrades to a logged warning rather than poisoning the transaction.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AuditView

logger = logging.getLogger(__name__)

# C0 (except tab/newline/cr) + DEL + C1 control characters.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_MAX_LEN = 500


def sanitize(text: Any, *, limit: int = _MAX_LEN) -> str:
    """Strip control chars and truncate untrusted text (vCenter/NSX/error strings)."""
    if text is None:
        return ""
    cleaned = _CONTROL_CHARS.sub("", str(text))
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + "…(truncated)"
    return cleaned


def record_audit(
    session: AsyncSession,
    *,
    actor: str,
    operation: str,
    resource: str,
    params: dict | None = None,
    result: str = "success",
) -> None:
    """Add one audit row to ``session`` (committed by the caller's transaction).

    Best-effort: a construction failure is logged and swallowed so auditing
    never blocks the operation it records.
    """
    try:
        session.add(
            AuditView(
                actor=sanitize(actor, limit=128),
                operation=sanitize(operation, limit=128),
                resource=sanitize(resource, limit=255),
                params=params,
                result=result,
            )
        )
    except Exception:  # pragma: no cover - defensive; construction shouldn't fail
        logger.exception(
            "audit row construction failed (op=%s resource=%s) — continuing",
            operation,
            resource,
        )
