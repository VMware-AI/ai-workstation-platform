"""Tests for runtime.managed_runtime + lifespan wiring (PR-F F-1)."""

from __future__ import annotations

import pytest
from agent_platform_control import config
from agent_platform_control.app import create_app
from agent_platform_control.orchestrator import DeploymentWorker
from agent_platform_control.runtime import (
    RuntimeStartupError,
    _build_provisioner,
    _build_worker,
    managed_runtime,
)


def _clear_settings(monkeypatch):
    config.get_settings.cache_clear()


# ---------------------------------------------------------- _build_provisioner


def test_build_provisioner_fake():
    from agent_platform_control.orchestrator.fake import FakeProvisioner

    prov = _build_provisioner("fake")
    assert isinstance(prov, FakeProvisioner)


def test_build_provisioner_unknown_raises():
    with pytest.raises(RuntimeStartupError, match="unknown"):
        _build_provisioner("zoolander")


def test_build_provisioner_vmware_missing_env_raises(monkeypatch):
    """vmware factory fails loudly when required env vars are absent."""
    for var in (
        "AGENT_PLATFORM_VSPHERE_URL",
        "AGENT_PLATFORM_VSPHERE_USER",
        "AGENT_PLATFORM_VSPHERE_PASSWORD",
        "AGENT_PLATFORM_VSPHERE_TEMPLATE",
    ):
        monkeypatch.delenv(var, raising=False)
    config.get_settings.cache_clear()
    with pytest.raises(RuntimeStartupError, match="needs these env vars"):
        _build_provisioner("vmware")


def test_build_provisioner_vmware_bad_template_path_raises(monkeypatch, tmp_path):
    """vmware factory translates FileNotFoundError into a teaching message."""
    monkeypatch.setenv("AGENT_PLATFORM_VSPHERE_URL", "https://vc.invalid/sdk")
    monkeypatch.setenv("AGENT_PLATFORM_VSPHERE_USER", "svc")
    monkeypatch.setenv("AGENT_PLATFORM_VSPHERE_PASSWORD", "p")
    monkeypatch.setenv("AGENT_PLATFORM_VSPHERE_TEMPLATE", str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("AGENT_PLATFORM_VSPHERE_VERIFY_SSL", "false")
    config.get_settings.cache_clear()
    with pytest.raises(RuntimeStartupError, match="template path does not exist"):
        _build_provisioner("vmware")


def test_build_provisioner_vmware_constructs_when_env_set(monkeypatch, tmp_path):
    """Happy path: all env present + real template file → returns VmwareProvisioner."""
    from agent_platform_control.orchestrator.vmware import VmwareProvisioner

    template = tmp_path / "cloud-init.yaml"
    template.write_text("#cloud-config\nhostname: smoke\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_PLATFORM_VSPHERE_URL", "https://vc.invalid/sdk")
    monkeypatch.setenv("AGENT_PLATFORM_VSPHERE_USER", "svc")
    monkeypatch.setenv("AGENT_PLATFORM_VSPHERE_PASSWORD", "p")
    monkeypatch.setenv("AGENT_PLATFORM_VSPHERE_TEMPLATE", str(template))
    monkeypatch.setenv("AGENT_PLATFORM_VSPHERE_VERIFY_SSL", "false")
    config.get_settings.cache_clear()
    prov = _build_provisioner("vmware")
    assert isinstance(prov, VmwareProvisioner)


# ---------------------------------------------------------- managed_runtime


@pytest.mark.asyncio
async def test_managed_runtime_disabled_yields_no_worker(monkeypatch):
    monkeypatch.setenv("AGENT_PLATFORM_ENABLE_WORKER", "false")
    monkeypatch.setenv("AGENT_PLATFORM_PROVISIONER_KIND", "")
    _clear_settings(monkeypatch)
    settings = config.get_settings()

    async with managed_runtime(settings) as services:
        assert services["worker"] is None


@pytest.mark.asyncio
async def test_managed_runtime_starts_worker_when_enabled(monkeypatch):
    monkeypatch.setenv("AGENT_PLATFORM_ENABLE_WORKER", "true")
    monkeypatch.setenv("AGENT_PLATFORM_PROVISIONER_KIND", "fake")
    _clear_settings(monkeypatch)
    settings = config.get_settings()

    async with managed_runtime(settings) as services:
        worker = services["worker"]
        assert isinstance(worker, DeploymentWorker)
    # After context exit, worker is stopped — no clean way to assert from
    # outside without poking private state. The fact that the context
    # closes cleanly is sufficient for this layer; deeper integration tests
    # live in test_orchestrator_worker.


@pytest.mark.asyncio
async def test_managed_runtime_logs_and_continues_when_misconfigured(monkeypatch, caplog):
    """Decision 18 PR-F: a bad config logs + continues — ASGI must boot."""
    monkeypatch.setenv("AGENT_PLATFORM_ENABLE_WORKER", "true")
    monkeypatch.setenv("AGENT_PLATFORM_PROVISIONER_KIND", "")  # invalid (empty)
    _clear_settings(monkeypatch)
    settings = config.get_settings()

    with caplog.at_level("ERROR"):
        async with managed_runtime(settings) as services:
            assert services["worker"] is None
    assert any(
        "DeploymentWorker startup failed" in record.getMessage() for record in caplog.records
    )


def test_build_worker_rejects_invalid_ttyd_cidr(monkeypatch):
    """SEC-1 (建议1): _build_worker rejects a malformed / default-route
    TTYD_ALLOW_CIDR at startup (RuntimeStartupError → worker disabled + logged
    via managed_runtime) instead of letting every clone silently ValueError at
    runtime — operator sees the cause, not just 'deployments all mysteriously
    fail'. Validated before the provisioner/engine are built (no DB needed)."""
    monkeypatch.setenv("AGENT_PLATFORM_PROVISIONER_KIND", "fake")
    monkeypatch.setenv("AGENT_PLATFORM_WORKER_TTYD_ALLOW_CIDR", "0.0.0.0/0")
    config.get_settings.cache_clear()
    settings = config.get_settings()
    with pytest.raises(RuntimeStartupError, match="TTYD_ALLOW_CIDR"):
        _build_worker(settings)


def test_build_worker_normalises_and_wires_ttyd_cidr(monkeypatch):
    """Minor: the validated/normalised ttyd CIDR lands on the worker attribute
    that _process_item threads into every CloneSpec (worker.py: ttyd_allow_cidr=
    self._ttyd_allow_cidr). A bare host normalises to /32 on the way in."""
    monkeypatch.setenv("AGENT_PLATFORM_PROVISIONER_KIND", "fake")
    monkeypatch.setenv("AGENT_PLATFORM_WORKER_TTYD_ALLOW_CIDR", "10.0.0.5")
    config.get_settings.cache_clear()
    settings = config.get_settings()
    worker = _build_worker(settings)
    assert worker._ttyd_allow_cidr == "10.0.0.5/32"


@pytest.mark.asyncio
async def test_managed_runtime_records_worker_error_on_bad_config(monkeypatch):
    """Review MEDIUM: a wanted-but-failed worker leaves a signal in the runtime
    dict (→ /healthz/deep degraded), not just a log line indistinguishable from
    a deliberately-disabled worker."""
    monkeypatch.setenv("AGENT_PLATFORM_ENABLE_WORKER", "true")
    monkeypatch.setenv("AGENT_PLATFORM_PROVISIONER_KIND", "fake")
    monkeypatch.setenv("AGENT_PLATFORM_WORKER_TTYD_ALLOW_CIDR", "0.0.0.0/0")
    _clear_settings(monkeypatch)
    settings = config.get_settings()
    async with managed_runtime(settings) as services:
        assert services["worker"] is None
        assert "TTYD_ALLOW_CIDR" in str(services.get("worker_error"))


@pytest.mark.asyncio
async def test_app_create_does_not_blow_up(monkeypatch):
    """Smoke: create_app() with default settings is importable + callable."""
    monkeypatch.setenv("AGENT_PLATFORM_ENABLE_WORKER", "false")
    _clear_settings(monkeypatch)
    app = create_app()
    # Routes registered, lifespan present
    paths = {route.path for route in app.routes}  # type: ignore[attr-defined]
    assert "/healthz" in paths


@pytest.mark.asyncio
async def test_lifespan_context_runs_via_asgi(monkeypatch):
    """End-to-end: trigger lifespan manually and confirm runtime lands on
    app.state. Mirrors what uvicorn does on real boot."""
    monkeypatch.setenv("AGENT_PLATFORM_ENABLE_WORKER", "false")
    _clear_settings(monkeypatch)
    app = create_app()

    # Manually drive the lifespan context — ASGITransport in httpx doesn't.
    async with app.router.lifespan_context(app):
        assert hasattr(app.state, "runtime")
        assert app.state.runtime["worker"] is None
