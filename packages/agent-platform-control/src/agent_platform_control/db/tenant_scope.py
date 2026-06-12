"""Multi-tenant SQL isolation helper (Decision 14).

Purpose
=======
Defense-in-depth against accidental cross-tenant data leaks. Every SELECT
that reads a tenant-owned table MUST go through :func:`tenant_scope`, which
injects a ``WHERE tenant_id = :tenant_id`` predicate.

This complements (does not replace) per-user owner_id filtering: even if an
endpoint forgets ``owner_id == user.user_id``, ``tenant_scope`` still blocks
the leak at the tenant boundary.

Usage
-----
.. code-block:: python

    from ..db.tenant_scope import tenant_scope

    stmt = tenant_scope(
        select(VM).where(VM.owner_id == user.user_id),
        tenant_id=user.tenant_id,
        model=VM,
    )

Admin bypass
------------
Some models are intentionally cross-tenant (catalog data, audit mirror).
They live in :data:`ADMIN_ONLY_MODELS` and ``tenant_scope`` refuses to wrap
them — callers must use a plain ``select(...)`` and gate access with
``require_admin``.

For *tenant-owned* tables read by an admin (e.g. an admin reviewing all
tenants' deployments), call sites use a plain ``select(...)`` directly and
the lint script (``scripts/check_tenant_scope.sh``) requires the file to be
in the admin-bypass whitelist.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select
from sqlalchemy.sql import ColumnElement

from .models import (
    VM,
    AuditView,
    Base,
    Deployment,
    ImageVersion,
    TokenUsage,
    Upgrade,
    User,
)

__all__ = [
    "ADMIN_ONLY_MODELS",
    "TENANT_SCOPED_MODELS",
    "TenantScopeError",
    "requires_tenant_scope",
    "tenant_scope",
]


class TenantScopeError(ValueError):
    """Raised when tenant_scope is misused.

    Examples: called on an admin-only model, called without a tenant_id,
    or called on a model whose primary entity can't be inferred.
    """


# Models that intentionally don't carry tenant_id — admin-only catalogs and
# audit mirror. Calling tenant_scope() on these raises TenantScopeError so
# the caller is forced to think about access control instead of silently
# getting a no-op filter.
ADMIN_ONLY_MODELS: frozenset[type[Base]] = frozenset(
    {
        ImageVersion,  # global image catalog, admin curates
        AuditView,  # mirror of vmware-policy audit.db, admin-only
    }
)


# Tenant-owned tables. Every read of these by a non-admin endpoint must go
# through tenant_scope(). DeploymentItem / UpgradeVM don't carry tenant_id
# directly — they're reached via the parent Deployment / Upgrade row, which
# is scoped. We list them here so requires_tenant_scope() returns False
# (callers join through the parent instead).
TENANT_SCOPED_MODELS: frozenset[type[Base]] = frozenset(
    {
        User,
        VM,
        Deployment,
        Upgrade,
        TokenUsage,
    }
)


def requires_tenant_scope(model: type[Base]) -> bool:
    """Return True if ``model`` has a ``tenant_id`` column and is not admin-only.

    Use this in lint / audit scripts to decide whether a raw ``select(model)``
    is allowed.
    """
    if model in ADMIN_ONLY_MODELS:
        return False
    return hasattr(model, "tenant_id")


def _infer_model(stmt: Select[Any]) -> type[Base]:
    """Best-effort: pull the primary mapped entity out of a Select.

    SQLAlchemy 2.x stores entities on the compile state; ``column_descriptions``
    is the public surface. We take the first entity whose ``type`` is a mapped
    class — handles both ``select(Model)`` and ``select(Model.col, ...)``.
    """
    for desc in stmt.column_descriptions:
        entity = desc.get("entity")
        if entity is not None and isinstance(entity, type) and issubclass(entity, Base):
            return entity
    raise TenantScopeError("could not infer model from stmt — pass model=... explicitly")


def tenant_scope(
    stmt: Select[Any],
    *,
    tenant_id: str,
    model: type[Base] | None = None,
) -> Select[Any]:
    """Augment ``stmt`` with ``WHERE <model>.tenant_id == tenant_id``.

    :param stmt: a SQLAlchemy 2.x ``Select`` statement.
    :param tenant_id: the caller's tenant id (from ``CurrentUser.tenant_id``).
        Must be a non-empty string — empty / None tenant_id would degenerate
        to an unconditional filter and is rejected.
    :param model: the model whose ``tenant_id`` column to filter on. If
        omitted, inferred from the statement's primary entity.
    :returns: a new ``Select`` with the filter applied. Original is unchanged
        (SQLAlchemy's ``.where`` returns a copy — immutability preserved).

    :raises TenantScopeError: if model is admin-only, has no ``tenant_id``
        column, or tenant_id is empty.
    """
    if not tenant_id:
        raise TenantScopeError(
            "tenant_scope requires a non-empty tenant_id "
            "(caller likely forgot to resolve CurrentUser.tenant_id)"
        )

    target = model if model is not None else _infer_model(stmt)

    if target in ADMIN_ONLY_MODELS:
        raise TenantScopeError(
            f"{target.__name__} is admin-only and has no tenant_id column; "
            "use a plain select(...) gated by require_admin"
        )

    tenant_col: ColumnElement[Any] | None = getattr(target, "tenant_id", None)
    if tenant_col is None:
        raise TenantScopeError(f"{target.__name__} has no tenant_id column; cannot tenant-scope it")

    return stmt.where(tenant_col == tenant_id)
