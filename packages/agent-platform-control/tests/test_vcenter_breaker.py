"""Circuit breaker on vCenter SmartConnect (harness H-14, #215).

Issue success criteria: N consecutive vCenter connect failures trip the
breaker → subsequent calls fast-fail with a clear 'circuit open' teaching
error instead of N×timeout. Per-target breakers so one dead vCenter does
not fast-fail the others (M2 multi-vCenter posture).
"""
# ruff: noqa: N802 — test fakes mirror pyVmomi API casing

# test fakes mirror pyVmomi API casing

from __future__ import annotations

import pytest
from agent_platform_control.orchestrator import vmware as vmware_mod
from agent_platform_telemetry_shim import CircuitBreakerError


@pytest.fixture(autouse=True)
def _fresh_breakers():
    """Each test starts with clean breaker + SI-cache state."""
    vmware_mod._BREAKERS.clear()
    vmware_mod._SI_CACHE.clear()
    vmware_mod._SI_META.clear()
    vmware_mod._SI_LOCKS.clear()
    yield
    vmware_mod._BREAKERS.clear()
    vmware_mod._SI_CACHE.clear()
    vmware_mod._SI_META.clear()
    vmware_mod._SI_LOCKS.clear()


def _target(host: str = "vc1.example.invalid") -> vmware_mod._Target:
    return vmware_mod._Target(host=host, port=443, user="svc", password="pw", verify_ssl=False)


def _failing_smartconnect(monkeypatch, counter: list[int]):
    def boom(**kwargs):
        counter.append(1)
        raise ConnectionError("connect timed out")

    monkeypatch.setattr(vmware_mod.vim_connect, "SmartConnect", boom)


def test_breaker_opens_after_three_consecutive_failures(monkeypatch):
    calls: list[int] = []
    _failing_smartconnect(monkeypatch, calls)

    for _ in range(3):
        with pytest.raises(ConnectionError):
            vmware_mod._connect(_target())
    assert len(calls) == 3

    # 4th attempt fast-fails: SmartConnect is NOT called again.
    with pytest.raises(CircuitBreakerError) as excinfo:
        vmware_mod._connect(_target())
    assert len(calls) == 3
    message = str(excinfo.value)
    assert "vc1.example.invalid" in message  # teaching: names the target
    assert "breaker is open" in message


def test_breakers_are_per_target(monkeypatch):
    calls: list[int] = []
    _failing_smartconnect(monkeypatch, calls)

    for _ in range(3):
        with pytest.raises(ConnectionError):
            vmware_mod._connect(_target("vc1.example.invalid"))
    with pytest.raises(CircuitBreakerError):
        vmware_mod._connect(_target("vc1.example.invalid"))

    # A different vCenter still gets a real connection attempt.
    with pytest.raises(ConnectionError):
        vmware_mod._connect(_target("vc2.example.invalid"))
    assert len(calls) == 4


def test_probe_after_cooldown_recovers(monkeypatch):
    calls: list[int] = []
    _failing_smartconnect(monkeypatch, calls)

    for _ in range(3):
        with pytest.raises(ConnectionError):
            vmware_mod._connect(_target())
    with pytest.raises(CircuitBreakerError):
        vmware_mod._connect(_target())

    # Cool-down elapses → next call is the probe; vCenter is back.
    breaker = vmware_mod._BREAKERS[("vc1.example.invalid:443", "svc")]
    monkeypatch.setattr(
        vmware_mod.time, "monotonic", lambda: breaker._opened_at + breaker.reset_after_s + 1
    )

    class _FakeSI:
        def RetrieveContent(self):
            return object()

    monkeypatch.setattr(vmware_mod.vim_connect, "SmartConnect", lambda **kw: _FakeSI())
    si = vmware_mod._connect(_target())
    assert isinstance(si, _FakeSI)
    assert breaker.state == "closed"


def test_success_resets_failure_count(monkeypatch):
    """Two failures, one success, two failures — breaker must stay closed
    (only CONSECUTIVE failures trip it)."""
    state = {"fail": True}

    class _FakeSI:
        def RetrieveContent(self):
            return object()

    def flaky(**kwargs):
        if state["fail"]:
            raise ConnectionError("connect timed out")
        return _FakeSI()

    monkeypatch.setattr(vmware_mod.vim_connect, "SmartConnect", flaky)

    for _ in range(2):
        with pytest.raises(ConnectionError):
            vmware_mod._connect(_target())
    state["fail"] = False
    vmware_mod._connect(_target())
    vmware_mod._SI_CACHE.clear()  # force reconnect path
    state["fail"] = True
    for _ in range(2):
        with pytest.raises(ConnectionError):
            vmware_mod._connect(_target())
    # Still closed: 2 + 2 non-consecutive failures < threshold reset by success.
    breaker = vmware_mod._BREAKERS[("vc1.example.invalid:443", "svc")]
    assert breaker.state == "closed"


def test_health_probe_bypasses_open_breaker(monkeypatch):
    """The admin health probe must show live vCenter state even while the
    provisioning breaker is open (PR #221 review HIGH-2)."""
    calls: list[int] = []
    _failing_smartconnect(monkeypatch, calls)

    for _ in range(3):
        with pytest.raises(ConnectionError):
            vmware_mod._connect(_target())
    with pytest.raises(CircuitBreakerError):
        vmware_mod._connect(_target())
    assert len(calls) == 3

    # vCenter is back; diagnostic path connects for real despite open breaker.
    class _FakeSI:
        def RetrieveContent(self):
            return object()

    monkeypatch.setattr(vmware_mod.vim_connect, "SmartConnect", lambda **kw: _FakeSI())
    si = vmware_mod._connect(_target(), bypass_breaker=True)
    assert isinstance(si, _FakeSI)


def test_health_probe_failures_do_not_trip_breaker(monkeypatch):
    calls: list[int] = []
    _failing_smartconnect(monkeypatch, calls)

    for _ in range(5):
        with pytest.raises(ConnectionError):
            vmware_mod._connect(_target(), bypass_breaker=True)
    # Normal path still attempts a real connection — breaker never consulted
    # by the diagnostic calls above.
    with pytest.raises(ConnectionError):
        vmware_mod._connect(_target())
    assert len(calls) == 6


@pytest.mark.asyncio
async def test_clone_vm_surfaces_breaker_teaching_error(monkeypatch, tmp_path):
    """End-to-end contract: an open breaker reaches the CloneResult the
    orchestrator records, teaching message intact (PR #221 review LOW-5)."""
    calls: list[int] = []
    _failing_smartconnect(monkeypatch, calls)
    for _ in range(3):
        with pytest.raises(ConnectionError):
            vmware_mod._connect(_target("vc.example"))

    tpl = tmp_path / "user-data.yaml.tpl"
    tpl.write_text("#cloud-config\n")
    prov = vmware_mod.VmwareProvisioner(
        vcenter_url="https://vc.example/sdk",
        vcenter_user="svc",
        vcenter_password="pw",
        cloud_init_template_path=tpl,
        verify_ssl=False,
    )
    from agent_platform_control.orchestrator.protocol import CloneSpec

    result = await prov.clone_vm(
        CloneSpec(
            intended_name="vm-x",
            template="any",
            tenant_id="t",
            owner_id="u",
            owner_login="u",
            image_version="v0.1.0",
            registry_url="r",
            goose_image_tag="1",
            litellm_gateway_url="http://gw",
            user_token="tok",
            heartbeat_url="http://hb",
        )
    )
    assert result.success is False
    assert "breaker is open" in result.error
    assert len(calls) == 3  # clone attempt fast-failed without a 4th connect


def test_hung_connect_on_one_target_blocks_neither_other_targets_nor_fast_fail(monkeypatch):
    """#226: the old single global _SI_LOCK was held across SmartConnect
    (~1-2s real-world), so a dead vCenter serialized everyone behind it —
    healthy targets AND the breaker's supposedly-instant fast-fail."""
    import threading

    hang = threading.Event()
    entered = threading.Event()

    def smart_connect(**kwargs):
        if kwargs["host"] == "vc-hung.example.invalid":
            entered.set()
            hang.wait(timeout=10)
            raise ConnectionError("eventually timed out")
        return object()  # healthy target connects instantly

    monkeypatch.setattr(vmware_mod.vim_connect, "SmartConnect", smart_connect)

    # Pre-trip vc-down's breaker so its fast-fail path is live.
    def boom(**kwargs):
        raise ConnectionError("down")

    monkeypatch.setattr(vmware_mod.vim_connect, "SmartConnect", boom)
    for _ in range(3):
        with pytest.raises(ConnectionError):
            vmware_mod._connect(_target("vc-down.example.invalid"))
    monkeypatch.setattr(vmware_mod.vim_connect, "SmartConnect", smart_connect)

    # Hold vc-hung's connect open on a worker thread.
    hung_result: list[BaseException] = []

    def connect_hung():
        try:
            vmware_mod._connect(_target("vc-hung.example.invalid"))
        except BaseException as e:  # recorded for assertion
            hung_result.append(e)

    t = threading.Thread(target=connect_hung, daemon=True)
    t.start()
    assert entered.wait(timeout=5), "hung connect never started"

    # While vc-hung's SmartConnect is blocked: a healthy target connects…
    done = threading.Event()
    healthy_si: list[object] = []

    def connect_healthy():
        healthy_si.append(vmware_mod._connect(_target("vc-ok.example.invalid")))
        done.set()

    t2 = threading.Thread(target=connect_healthy, daemon=True)
    t2.start()
    assert done.wait(timeout=2), "healthy target was blocked behind the hung connect"
    assert healthy_si

    # …and vc-down's breaker fast-fails instantly (not queued behind the hang).
    ff_done = threading.Event()
    ff_result: list[BaseException] = []

    def fast_fail():
        try:
            vmware_mod._connect(_target("vc-down.example.invalid"))
        except CircuitBreakerError as e:
            ff_result.append(e)
        ff_done.set()

    t3 = threading.Thread(target=fast_fail, daemon=True)
    t3.start()
    assert ff_done.wait(timeout=2), "breaker fast-fail was blocked behind the hung connect"
    assert ff_result and "breaker is open" in str(ff_result[0])

    hang.set()
    t.join(timeout=5)
