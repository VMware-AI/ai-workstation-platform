"""Unit tests for agent_platform_repo.cleanup.

Same MockTransport pattern as test_index.py — no real devpi needed.
The cleanup orchestrator's "stop on first failure" contract is the
most important behavior here; without it a partial wipe could continue
silently after a permission denial.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from agent_platform_repo.cleanup import (
    CleanupPlan,
    cleanup,
    delete_version,
    find_obsolete_versions,
)

INDEX = "root/agent-platform"


def _mock_client(handler: Any) -> httpx.Client:
    return httpx.Client(
        base_url="http://devpi.example",
        transport=httpx.MockTransport(handler),
        headers={"Accept": "application/json"},
    )


# ---------------------------------------------------------------------------
# find_obsolete_versions (pure)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_find_obsolete_versions_basic() -> None:
    plan = find_obsolete_versions(["1.3.0", "1.2.0", "1.1.0", "1.0.0"], retain=2)
    assert plan.keep == ("1.3.0", "1.2.0")
    assert plan.drop == ("1.1.0", "1.0.0")


@pytest.mark.unit
def test_find_obsolete_versions_retain_zero_wipes_all() -> None:
    plan = find_obsolete_versions(["2.0.0", "1.0.0"], retain=0)
    assert plan.keep == ()
    assert plan.drop == ("2.0.0", "1.0.0")


@pytest.mark.unit
def test_find_obsolete_versions_retain_exceeds_count_drops_nothing() -> None:
    plan = find_obsolete_versions(["1.0.0"], retain=5)
    assert plan.keep == ("1.0.0",)
    assert plan.drop == ()


@pytest.mark.unit
def test_find_obsolete_versions_negative_retain_raises() -> None:
    with pytest.raises(ValueError):
        find_obsolete_versions(["1.0.0"], retain=-1)


# ---------------------------------------------------------------------------
# delete_version
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_delete_version_success() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "DELETE"
        assert req.url.path == f"/{INDEX}/p/1.0.0"
        return httpx.Response(204)

    with _mock_client(handler) as http:
        ok, detail = delete_version(http, "p", "1.0.0", index=INDEX)
    assert ok is True
    assert "204" in detail


@pytest.mark.unit
def test_delete_version_non_2xx_reports_status() -> None:
    with _mock_client(lambda _: httpx.Response(403, text="forbidden")) as http:
        ok, detail = delete_version(http, "p", "1.0.0", index=INDEX)
    assert ok is False
    assert "403" in detail
    assert "forbidden" in detail


@pytest.mark.unit
def test_delete_version_network_error_does_not_raise() -> None:
    def boom(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated")

    with _mock_client(boom) as http:
        ok, detail = delete_version(http, "p", "1.0.0", index=INDEX)
    assert ok is False
    assert "network error" in detail


# ---------------------------------------------------------------------------
# cleanup (orchestrator)
# ---------------------------------------------------------------------------


class _Devpi:
    """Fake devpi handler stateful enough to test cleanup walks.

    Tracks DELETE calls so tests can assert the orchestrator stopped on
    the first failure rather than continuing past it.
    """

    def __init__(
        self,
        projects: dict[str, list[str]],
        *,
        fail_on: tuple[str, str] | None = None,
    ) -> None:
        self.projects = projects
        self.fail_on = fail_on
        self.deletes_attempted: list[tuple[str, str]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        parts = [p for p in req.url.path.split("/") if p]
        if req.method == "GET" and parts == ["root", "agent-platform"]:
            return httpx.Response(200, json={"result": dict.fromkeys(self.projects, "...")})
        if req.method == "GET" and len(parts) == 3 and parts[2] in self.projects:
            return httpx.Response(
                200,
                json={"result": {v: {} for v in self.projects[parts[2]]}},
            )
        if req.method == "DELETE" and len(parts) == 4:
            target = (parts[2], parts[3])
            self.deletes_attempted.append(target)
            if self.fail_on == target:
                return httpx.Response(403, text="denied")
            return httpx.Response(204)
        return httpx.Response(404, text=f"unhandled {req.method} {req.url.path}")


@pytest.mark.unit
def test_cleanup_dry_run_lists_but_deletes_nothing() -> None:
    state = _Devpi({"p-a": ["1.2.0", "1.1.0", "1.0.0"]})
    with _mock_client(state) as http:
        plans, report = cleanup(http, index=INDEX, retain=1, dry_run=True)
    assert state.deletes_attempted == []
    assert report.deleted == ()
    assert report.failed == {}
    assert len(plans) == 1
    assert plans[0] == CleanupPlan(project="p-a", keep=("1.2.0",), drop=("1.1.0", "1.0.0"))


@pytest.mark.unit
def test_cleanup_deletes_obsolete_and_returns_report() -> None:
    state = _Devpi({"p-a": ["2.0.0", "1.0.0"], "p-b": ["3.0.0"]})
    with _mock_client(state) as http:
        plans, report = cleanup(http, index=INDEX, retain=1)
    # p-a drops 1.0.0; p-b has 1 version, drops nothing.
    assert state.deletes_attempted == [("p-a", "1.0.0")]
    assert report.deleted == ("p-a 1.0.0",)
    assert report.failed == {}
    # Plans still show both projects so logs assert "we considered each one".
    assert {p.project for p in plans} == {"p-a", "p-b"}


@pytest.mark.unit
def test_cleanup_stops_on_first_failure_and_does_not_attempt_remaining() -> None:
    state = _Devpi(
        {"p-a": ["3.0.0", "2.0.0", "1.0.0"]},
        fail_on=("p-a", "2.0.0"),
    )
    with _mock_client(state) as http:
        _plans, report = cleanup(http, index=INDEX, retain=1)
    # Should have tried 2.0.0 (the first older one) and stopped — 1.0.0 not touched.
    assert state.deletes_attempted == [("p-a", "2.0.0")]
    assert report.deleted == ()
    assert "p-a 2.0.0" in report.failed
    assert "403" in report.failed["p-a 2.0.0"]


@pytest.mark.unit
def test_cleanup_skips_projects_that_vanish_between_list_and_versions() -> None:
    """Devpi lists project P, but the per-project GET 404s (race).

    The orchestrator must skip P quietly rather than abort the whole
    sweep — other projects probably still need cleanup.
    """

    def handler(req: httpx.Request) -> httpx.Response:
        parts = [p for p in req.url.path.split("/") if p]
        if req.method == "GET" and parts == ["root", "agent-platform"]:
            return httpx.Response(200, json={"result": {"ghost": "...", "live": "..."}})
        if req.method == "GET" and parts == ["root", "agent-platform", "ghost"]:
            return httpx.Response(404, text="race-condition gone")
        if req.method == "GET" and parts == ["root", "agent-platform", "live"]:
            return httpx.Response(200, json={"result": {"2.0.0": {}, "1.0.0": {}}})
        if req.method == "DELETE" and parts == ["root", "agent-platform", "live", "1.0.0"]:
            return httpx.Response(204)
        return httpx.Response(500)

    with _mock_client(handler) as http:
        plans, report = cleanup(http, index=INDEX, retain=1)
    assert [p.project for p in plans] == ["live"]
    assert report.deleted == ("live 1.0.0",)
