"""Unit tests for agent_platform_repo.index.

The devpi server is faked via ``httpx.MockTransport`` so these tests
run with no external service. The real devpi protocol (PUT/POST for
write, GET for read) is covered by the integration test which is gated
on a devpi container being available locally — CI does not.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from agent_platform_repo.index import (
    RepoIndexError,
    list_projects,
    list_versions,
)

INDEX = "root/agent-platform"


def _mock_client(handler: Any) -> httpx.Client:
    return httpx.Client(
        base_url="http://devpi.example",
        transport=httpx.MockTransport(handler),
        headers={"Accept": "application/json"},
    )


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_list_projects_returns_sorted_names() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/{INDEX}/"
        return httpx.Response(
            200,
            json={"result": {"runtime-y": "...", "alpha-x": "...", "mid-w": "..."}},
        )

    with _mock_client(handler) as http:
        names = list_projects(http, index=INDEX)
    assert names == ["alpha-x", "mid-w", "runtime-y"]


@pytest.mark.unit
def test_list_projects_empty_index() -> None:
    with _mock_client(lambda _: httpx.Response(200, json={"result": {}})) as http:
        assert list_projects(http, index=INDEX) == []


@pytest.mark.unit
def test_list_projects_non_2xx_raises_teaching_error() -> None:
    with _mock_client(lambda _: httpx.Response(500, text="bang")) as http:
        with pytest.raises(RepoIndexError) as exc:
            list_projects(http, index=INDEX)
        # Error should name the path and the status so the operator can grep logs.
        assert f"/{INDEX}/" in str(exc.value)
        assert "500" in str(exc.value)


@pytest.mark.unit
def test_list_projects_malformed_body_raises() -> None:
    with (
        _mock_client(lambda _: httpx.Response(200, json={"result": "not-a-dict"})) as http,
        pytest.raises(RepoIndexError),
    ):
        list_projects(http, index=INDEX)


# ---------------------------------------------------------------------------
# list_versions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_list_versions_pep440_sort_desc() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/{INDEX}/agent-runtime"
        return httpx.Response(
            200,
            json={"result": {"1.10.0": {}, "1.2.0": {}, "1.9.0": {}, "0.1.0": {}}},
        )

    with _mock_client(handler) as http:
        versions = list_versions(http, "agent-runtime", index=INDEX)
    # PEP 440-aware: 1.10.0 > 1.9.0, not lexicographic.
    assert versions == ["1.10.0", "1.9.0", "1.2.0", "0.1.0"]


@pytest.mark.unit
def test_list_versions_malformed_versions_sink_to_bottom() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"result": {"1.0.0": {}, "draft-branch": {}, "2.0.0": {}}},
        )

    with _mock_client(handler) as http:
        versions = list_versions(http, "p", index=INDEX)
    # Canonical PEP 440 first (newest-first), malformed at the end.
    assert versions[:2] == ["2.0.0", "1.0.0"]
    assert versions[-1] == "draft-branch"


@pytest.mark.unit
def test_list_versions_404_teaches_the_operator() -> None:
    with _mock_client(lambda _: httpx.Response(404, text="not found")) as http:
        with pytest.raises(RepoIndexError) as exc:
            list_versions(http, "ghost-pkg", index=INDEX)
        msg = str(exc.value)
        assert "unknown project" in msg
        assert "ghost-pkg" in msg
        assert "upload first" in msg


@pytest.mark.unit
def test_list_versions_non_404_non_200_raises() -> None:
    with _mock_client(lambda _: httpx.Response(503, text="overload")) as http:
        with pytest.raises(RepoIndexError) as exc:
            list_versions(http, "p", index=INDEX)
        assert "503" in str(exc.value)


@pytest.mark.unit
def test_list_versions_empty_release_list() -> None:
    with _mock_client(lambda _: httpx.Response(200, json={"result": {}})) as http:
        assert list_versions(http, "p", index=INDEX) == []
