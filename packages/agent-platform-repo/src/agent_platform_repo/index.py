"""Read-only listing + search of the devpi index.

These helpers are deliberately thin wrappers over devpi's JSON API so the
M2 installer (C8) and admin tooling can ask "what versions of <pkg> are
available" without re-implementing parser logic. All functions take an
``httpx.Client`` to keep them unit-testable via ``httpx.MockTransport``;
the CLI (cli.py) builds the client via :func:`agent_platform_repo.cli._client`.

devpi exposes:
    GET /<user>/<index>/                       -> list of projects
    GET /<user>/<index>/<project>              -> project detail w/ versions
    GET /<user>/<index>/<project>/<version>    -> per-version file links

We rely only on the first two and parse ``result.projects`` / ``result.versions``
(devpi's JSON contract since 6.x).
"""

from __future__ import annotations

from typing import Final

import httpx

# devpi returns a flat dict at /<index>/ with a 'result' key that maps
# project_name -> project_url. The keys are PEP 503 normalized names.
_PROJECTS_KEY: Final = "result"


class RepoIndexError(RuntimeError):
    """Raised when devpi answers with a non-2xx or an unexpected shape."""


def list_projects(client: httpx.Client, *, index: str) -> list[str]:
    """Return PEP 503 normalized project names known to ``<index>``.

    Args:
        client: pre-built httpx Client (base_url = devpi root, Accept: json).
        index: devpi index path, e.g. ``"root/agent-platform"``.

    Returns:
        Sorted list of project names. Empty list if the index has no
        projects (fresh index after ``init``).

    Raises:
        RepoIndexError: devpi returned non-2xx or a malformed body.
    """
    resp = client.get(f"/{index}/")
    if resp.status_code != 200:
        raise RepoIndexError(f"devpi GET /{index}/ -> {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    projects = body.get(_PROJECTS_KEY)
    if not isinstance(projects, dict):
        raise RepoIndexError(f"devpi /{index}/ returned {type(projects).__name__}, expected dict")
    return sorted(projects.keys())


def list_versions(client: httpx.Client, project: str, *, index: str) -> list[str]:
    """Return the versions of ``project`` on ``<index>``, newest-first.

    Sort is PEP 440 aware (uses :class:`packaging.version.Version`), so
    ``["1.10.0", "1.9.0", "1.2.0"]`` not lexicographic ``["1.9.0", ...]``.

    Args:
        client: pre-built httpx Client.
        project: PEP 503 normalized project name (devpi will normalize on
            its end but consistency helps the cache).
        index: devpi index path.

    Returns:
        Versions newest-first. Empty list if devpi knows the project but
        has no releases (rare; typically a pre-upload placeholder).

    Raises:
        RepoIndexError: devpi returned non-2xx, or the project does not exist
            on the index. Distinguish via the message: 404 means "unknown
            project" and is included as the prefix.
    """
    resp = client.get(f"/{index}/{project}")
    if resp.status_code == 404:
        raise RepoIndexError(
            f"404 unknown project '{project}' on /{index} (check spelling or upload first)"
        )
    if resp.status_code != 200:
        raise RepoIndexError(
            f"devpi GET /{index}/{project} -> {resp.status_code}: {resp.text[:200]}"
        )
    body = resp.json()
    result = body.get(_PROJECTS_KEY)
    if not isinstance(result, dict):
        raise RepoIndexError(
            f"devpi /{index}/{project} returned {type(result).__name__}, expected dict"
        )
    versions = list(result.keys())
    return _sort_versions_desc(versions)


def _sort_versions_desc(versions: list[str]) -> list[str]:
    """Sort versions newest-first using PEP 440 semantics; fall back to
    lexicographic for malformed strings (devpi sometimes carries upload
    drafts with non-canonical tags).
    """
    # Import is local: keeps module-import cheap when only list_projects is
    # called (e.g. the admin "is anything here" probe).
    from packaging.version import InvalidVersion, Version

    # Wrap so the first tuple element keeps "malformed sinks to bottom" under
    # reverse=True: valid versions get bucket 1 (sorted at the top under
    # reverse=desc), malformed get bucket 0 (sorted at the bottom).
    def _key(v: str) -> tuple[int, Version | str]:
        try:
            return (1, Version(v))
        except InvalidVersion:
            return (0, v)

    return sorted(versions, key=_key, reverse=True)
