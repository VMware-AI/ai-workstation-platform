"""Cleanup obsolete releases on the devpi index.

Customer sites have bounded disk. After dozens of patch releases the
``root/agent-platform`` index will accumulate stale wheels. This module
implements a "retain N latest versions per project" sweep that an ops
cron can run safely (read first, then delete; no force flags).

Function shape mirrors :mod:`agent_platform_repo.index` — pure functions
take a pre-built ``httpx.Client`` so they unit-test cleanly under
``httpx.MockTransport``. The CLI wrapper lives in ``cli.py``.

Safety contract:
    * ``find_obsolete_versions`` is pure (no I/O) so the caller can
      preview the deletion list and confirm before any DELETE is sent.
    * ``delete_version`` issues one DELETE per version and returns the
      HTTP outcome; nothing is wrapped in a "best-effort" silent path.
    * ``cleanup`` (orchestrator) is the only function that loops; it
      stops on the first non-2xx so partial states are visible to the
      operator.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from .index import RepoIndexError, list_projects, list_versions


@dataclass(frozen=True)
class CleanupPlan:
    """Per-project deletion preview.

    ``keep`` keeps the call site's intent visible alongside ``drop``;
    even when the entire list is below the retain threshold (``drop``
    empty), having the kept list lets the operator log assert the count.
    """

    project: str
    keep: tuple[str, ...]
    drop: tuple[str, ...]


@dataclass(frozen=True)
class CleanupReport:
    """Result of :func:`cleanup` — the actual deletions and their outcomes.

    ``failed`` is keyed by ``f"{project} {version}"`` so a deletion
    failure on one project does not collide with a different project's
    failing version.
    """

    deleted: tuple[str, ...] = field(default_factory=tuple)
    failed: dict[str, str] = field(default_factory=dict)


def find_obsolete_versions(versions_desc: list[str], retain: int) -> CleanupPlan:
    """Pure helper: given a newest-first version list, split into keep/drop.

    Args:
        versions_desc: versions in newest-first order (e.g. as returned
            by :func:`agent_platform_repo.index.list_versions`).
        retain: how many of the newest versions to keep.

    Returns:
        :class:`CleanupPlan` with the project name set to ``""`` —
        the caller fills it in. Splitting that detail out keeps the
        function I/O-free and trivially testable.
    """
    if retain < 0:
        raise ValueError(f"retain must be >= 0, got {retain}")
    if retain == 0:
        # Operator wants to wipe everything. Honor it but make it loud.
        return CleanupPlan(project="", keep=(), drop=tuple(versions_desc))
    keep = tuple(versions_desc[:retain])
    drop = tuple(versions_desc[retain:])
    return CleanupPlan(project="", keep=keep, drop=drop)


def delete_version(
    client: httpx.Client, project: str, version: str, *, index: str
) -> tuple[bool, str]:
    """Issue DELETE for one ``(project, version)`` on ``<index>``.

    devpi accepts ``DELETE /<index>/<project>/<version>``.

    Returns:
        ``(success, detail)`` — detail carries the HTTP status text on
        success and the error body excerpt on failure.

    Raises:
        Never. Network errors return ``(False, str(error))`` so the
        orchestrator can keep going under ``--keep-going`` (future flag).
        Today the orchestrator stops on first failure; we keep the
        non-raising shape so behaviour is symmetric whichever way the
        flag goes later.
    """
    try:
        resp = client.delete(f"/{index}/{project}/{version}")
    except httpx.HTTPError as e:
        return (False, f"network error: {e}")

    if resp.status_code in (200, 204):
        return (True, f"deleted (HTTP {resp.status_code})")
    return (False, f"HTTP {resp.status_code}: {resp.text[:200]}")


def cleanup(
    client: httpx.Client, *, index: str, retain: int, dry_run: bool = False
) -> tuple[list[CleanupPlan], CleanupReport]:
    """Walk the index, build per-project plans, and (unless ``dry_run``) delete.

    Args:
        client: httpx Client carrying creds and ``Accept: application/json``.
        index: devpi index path, e.g. ``"root/agent-platform"``.
        retain: how many newest versions to retain per project.
        dry_run: if True, return the plans but do not DELETE.

    Returns:
        ``(plans, report)``. Plans are always populated (preview value);
        the report is empty in ``dry_run`` mode.

    Stops on first delete failure (sets ``report.failed`` then returns)
    so the operator can investigate before continuing.
    """
    projects = list_projects(client, index=index)
    plans: list[CleanupPlan] = []
    for project in projects:
        try:
            versions = list_versions(client, project, index=index)
        except RepoIndexError:
            # Race: devpi reported the project at list time but it vanished
            # before we asked for versions. Skip — nothing to clean.
            continue
        plan = find_obsolete_versions(versions, retain)
        plans.append(CleanupPlan(project=project, keep=plan.keep, drop=plan.drop))

    if dry_run:
        return plans, CleanupReport()

    deleted: list[str] = []
    failed: dict[str, str] = {}
    for plan in plans:
        for version in plan.drop:
            ok, detail = delete_version(client, plan.project, version, index=index)
            if ok:
                deleted.append(f"{plan.project} {version}")
            else:
                failed[f"{plan.project} {version}"] = detail
                # Stop on first failure — see module docstring "Safety contract".
                return plans, CleanupReport(deleted=tuple(deleted), failed=failed)
    return plans, CleanupReport(deleted=tuple(deleted))
