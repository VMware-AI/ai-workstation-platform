"""End-to-end tests for the 10-step M1.26 acceptance demo.

See: docs/plans/2026-05-17-agent-platform-design.md §7

Each test follows arrange → act → assert. Tests that depend on hardware
(NSX, vSAN, GPU) are marked `hw_blocked` and auto-skip unless
`AGENT_PLATFORM_HW_AVAILABLE=1` is set. Tests blocked on still-in-flight components
use `pytest.skip(reason=...)` with a concrete pointer (PR / task id).

Status legend (column 4 in PR description):
- REAL  : runs real code & asserts behaviour
- HW    : skipped — needs real cluster
- WAIT  : skipped — waiting on another PR / sub-task
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

# ---------------------------------------------------------------------------
# Step 1 — admin uploads signed scale bundle, control verifies + registers in C4
# ---------------------------------------------------------------------------


@pytest.mark.demo_step(1)
def test_step01_scale_bundle_pack_and_hash(tmp_demo_dir: Path) -> None:
    """REAL: agent-platform-bundle pack + sha256 round-trip.

    The "upload + verify + register" flow needs the C1 upload endpoint
    (not implemented yet) — for now we exercise the offline packing path
    which IS the single source of truth for verify (see C9 cli.py docstring).
    """
    # Arrange — a fake "release" tree.
    src = tmp_demo_dir / "scale-v0.1.0"
    src.mkdir()
    (src / "manifest.yaml").write_text("name: scale\nversion: 0.1.0\n", encoding="utf-8")
    (src / "payload.txt").write_text("hello agent-platform\n", encoding="utf-8")
    out = tmp_demo_dir / "scale-v0.1.0.tar.zst"

    bundle_bin = shutil.which("agent-platform-bundle")
    if bundle_bin is None:
        pytest.skip("agent-platform-bundle CLI not on PATH (run `uv sync` at repo root)")

    # Act
    r1 = subprocess.run(
        [bundle_bin, "pack", str(src), str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    r2 = subprocess.run(
        [bundle_bin, "sha256", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )

    # Assert
    assert r1.returncode == 0, f"pack failed: {r1.stderr}"
    assert out.exists() and out.stat().st_size > 0
    assert r2.returncode == 0
    digest = r2.stdout.strip()
    assert len(digest) == 64, f"expected hex sha256, got {digest!r}"


# ---------------------------------------------------------------------------
# Step 2 — admin batch-creates 3 VMs for tenant-A under 10 minutes
# ---------------------------------------------------------------------------


@pytest.mark.demo_step(2)
@pytest.mark.asyncio
async def test_step02_batch_create_three_vms(
    control_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    agent_platform_env,  # type: ignore[no-untyped-def]
    created_deployments: list[str],
) -> None:
    """REAL (when wired): POST /v1/deployments + poll items to powered_on.

    Becomes runnable now that #103 vcsim provisioner is in main. Auto-
    skips on a laptop with nothing running. Requires three env vars
    when invoked against staging / real C1:

    - AGENT_PLATFORM_CONTROL_URL  → reachable C1 (default localhost:8000)
    - AGENT_PLATFORM_E2E_TENANT   → existing tenant id
    - AGENT_PLATFORM_E2E_USERS    → 3 comma-separated existing user ids in that tenant
    - AGENT_PLATFORM_E2E_TEMPLATE → vCenter template path (default "[t] agent-platform.vmtx")

    Pre-seeding users/tenants is intentionally out of scope — C1 has no
    admin user/tenant create endpoint (M1 stub auth), and seeding via
    the DB belongs in the staging Terraform, not the test.
    """
    import os

    # --- Skip gates ---------------------------------------------------------
    tenant_id = os.environ.get("AGENT_PLATFORM_E2E_TENANT")
    user_csv = os.environ.get("AGENT_PLATFORM_E2E_USERS")
    if not (tenant_id and user_csv):
        pytest.skip(
            "AGENT_PLATFORM_E2E_TENANT + AGENT_PLATFORM_E2E_USERS not set — staging-only test. "
            "Set both (with 3 comma-separated existing user ids) to run."
        )
    user_ids = [u.strip() for u in user_csv.split(",") if u.strip()]
    assert len(user_ids) == 3, f"need exactly 3 users for the demo, got {user_ids}"
    template = os.environ.get("AGENT_PLATFORM_E2E_TEMPLATE", "[t] agent-platform.vmtx")

    # Probe C1 health before sending real traffic so a missing control plane
    # produces a clean skip instead of a 60-second connection timeout.
    try:
        r = await control_client.get("/healthz", timeout=3.0)
        if r.status_code != 200:
            pytest.skip(f"C1 unhealthy at {agent_platform_env.control_url}: HTTP {r.status_code}")
    except httpx.HTTPError as e:
        pytest.skip(f"C1 unreachable at {agent_platform_env.control_url}: {e}")

    # --- Arrange: deployment request ---------------------------------------
    body = {
        "tenant_id": tenant_id,
        "template": template,
        "image_version": "v0.1.0",
        "items": [
            {"owner_id": uid, "intended_name": f"e2e-{uid[:8]}-{i}"}
            for i, uid in enumerate(user_ids)
        ],
    }

    # --- Act: submit + poll ------------------------------------------------
    r = await control_client.post("/v1/deployments", json=body, headers=admin_headers)
    assert r.status_code == 201, f"POST /v1/deployments returned {r.status_code}: {r.text}"
    deployment_id = r.json()["id"]
    # Register for session teardown so this VM trio gets reclaimed even if an
    # assertion below fails (see conftest `created_deployments`).
    created_deployments.append(deployment_id)

    # Demo acceptance: "≤ 10 分钟" default. Poll every 2 s; CI on real hardware
    # would easily fit; on vcsim it's typically <60 s but we keep the budget
    # honest. The deadline is overridable via AGENT_PLATFORM_E2E_DEPLOY_TIMEOUT_S
    # (the failure message below tells operators to raise it).
    import asyncio

    deadline_s = float(os.environ.get("AGENT_PLATFORM_E2E_DEPLOY_TIMEOUT_S", 10 * 60))
    interval_s = 2.0
    elapsed = 0.0
    final = None
    while elapsed < deadline_s:
        rr = await control_client.get(f"/v1/deployments/{deployment_id}", headers=admin_headers)
        assert rr.status_code == 200, rr.text
        dep = rr.json()
        if dep["state"] in {"completed", "failed", "partially_failed"}:
            final = dep
            break
        await asyncio.sleep(interval_s)
        elapsed += interval_s
    assert final is not None, (
        f"deployment {deployment_id} did not reach terminal state in {deadline_s}s; "
        "raise AGENT_PLATFORM_E2E_DEPLOY_TIMEOUT_S or check the orchestrator worker logs"
    )

    # --- Assert: all three items powered_on --------------------------------
    items = final.get("items") or []
    assert len(items) == 3, f"expected 3 items, got {len(items)}: {items}"
    states = [i["state"] for i in items]
    assert all(s == "powered_on" for s in states), (
        f"deployment {deployment_id} finished in {final['state']}; "
        f"per-item states: {states}; errors: "
        + "; ".join(filter(None, (i.get("error_message") for i in items)))
    )


# ---------------------------------------------------------------------------
# Step 3 — console circle diagram shows 3 green dots; NSX SG has 3 members
# ---------------------------------------------------------------------------


@pytest.mark.demo_step(3)
@pytest.mark.hw_blocked
def test_step03_console_circle_and_nsx_security_group() -> None:
    """HW: requires a real NSX-T manager + tenant-A Security Group.

    Verifies (when hardware is wired):
      - C2 circle layout returns 3 nodes for tenant-A
      - `tenant-A-vms` Security Group on NSX has effective members == 3
    """
    pytest.skip("NSX lab not ready (hw_blocked auto-skips when AGENT_PLATFORM_HW_AVAILABLE!=1)")
    # Arrange — playwright on console_page; httpx → vmware-nsx wrapper
    # Act    — read /api/topology/tenant/A; SG membership query
    # Assert — 3 nodes green; SG.member_count == 3


# ---------------------------------------------------------------------------
# Step 4 — Alice sees workspace + drops report.xlsx into outbox/
# ---------------------------------------------------------------------------


@pytest.mark.demo_step(4)
@pytest.mark.hw_blocked
def test_step04_fileshare_mount_and_drop_outbox() -> None:
    """HW: requires a real Samba/vSAN File Services SMB share.

    Verifies (when hardware is wired):
      - `\\\\fs.agent-platform.local\\u\\alice\\workspace` is reachable
      - writing report.xlsx into outbox/ succeeds and shows up via SMB list
    """
    pytest.skip("vSAN File Services SMB share not provisioned (hw_blocked)")
    # Arrange — SMB client (impacket / smbprotocol) connecting to env.fileshare_unc
    # Act    — open outbox/report.xlsx for write
    # Assert — listed; size matches


# ---------------------------------------------------------------------------
# Step 5 — Alice opens portal Web terminal & runs `agent run "..."`
# ---------------------------------------------------------------------------


@pytest.mark.demo_step(5)
def test_step05_portal_web_terminal_agent_run() -> None:
    """WAIT: portal SSO + ttyd integration pending (tasks 1.20 / 1.22).

    Once portal SSO lands, this test should:
      - login Alice via OIDC
      - open Web terminal for her VM
      - send "agent run '读 outbox/report.xlsx ...'"
      - assert command echoes back through xterm.js
    """
    pytest.skip("portal SSO + ttyd integration pending tasks 1.20 / 1.22")
    # Arrange — portal_page fixture + login flow
    # Act    — page.keyboard.type("agent run ...")
    # Assert — terminal buffer contains command


# ---------------------------------------------------------------------------
# Step 6 — within 30s, summary.md appears in alice/inbox/
# ---------------------------------------------------------------------------


@pytest.mark.demo_step(6)
@pytest.mark.hw_blocked
def test_step06_inbox_summary_appears_within_30s() -> None:
    """HW: needs GPU-backed LLM (C6 vLLM) + working agent runtime in the VM.

    Verifies (when hardware is wired):
      - inbox/summary.md exists within 30s of agent run completion
      - content is non-empty Chinese summary text
    """
    pytest.skip("GPU + vLLM not provisioned (hw_blocked)")
    # Arrange — poll loop on env.fileshare_unc + "/inbox/summary.md"
    # Act    — wait up to 30s
    # Assert — file exists, size > 0, contains CJK chars


# ---------------------------------------------------------------------------
# Step 7 — admin sees Alice's token usage on the console dashboard
# ---------------------------------------------------------------------------


@pytest.mark.demo_step(7)
async def test_step07_token_dashboard_renders(console_page) -> None:  # type: ignore[no-untyped-def]
    """REAL (when console reachable): admin opens TokenUsage page and sees KPI cards.

    Uses the playwright `console_page` fixture which auto-skips if the console
    URL is not reachable, so this test is safe in laptop / collection-only mode.
    """
    # Arrange — fixture already navigated to console root.
    # Act — navigate to the token usage view (route per C2 design).
    route = "/admin/token-usage"
    response = await console_page.goto(
        console_page.url.rstrip("/") + route,
        wait_until="domcontentloaded",
    )

    # Assert — the route actually resolved with a non-error status (an SPA error
    # boundary still containing the word "token" used to slip through the old
    # smoke check). A null response means a same-document navigation (SPA
    # client-side route) which is acceptable; a real HTTP response must be <400.
    if response is not None:
        assert response.status < 400, (
            f"{route} served HTTP {response.status} (error page); expected <400"
        )
    assert console_page.url.rstrip("/").endswith(route), (
        f"expected to land on {route}, got {console_page.url}"
    )

    # And the page must actually render the token-usage view, not just any page.
    body_text = await console_page.locator("body").inner_text()
    assert body_text.strip(), "console served an empty body"
    keywords = ("token", "usage", "Token", "用量")
    assert any(k in body_text for k in keywords), (
        f"no token-usage keyword found on page; got first 200 chars: {body_text[:200]!r}"
    )


# ---------------------------------------------------------------------------
# Step 8 — admin clicks "upgrade to v0.2.0", blue-green rollout
# ---------------------------------------------------------------------------


@pytest.mark.demo_step(8)
def test_step08_blue_green_upgrade() -> None:
    """WAIT: upgrade API skeleton not yet present (task 1.11).

    Once the /admin/upgrade endpoint lands, this test should:
      - POST {target_version: "0.2.0"}
      - poll status until "switched"
      - assert Alice's workspace data survives (sha256 of a sentinel file unchanged)
    """
    pytest.skip("upgrade API skeleton pending task 1.11")
    # Arrange — sentinel file in alice/workspace + sha256
    # Act    — POST /admin/upgrade; poll
    # Assert — status=switched; sha256 unchanged


# ---------------------------------------------------------------------------
# Step 9 — audit log records the above actions
# ---------------------------------------------------------------------------


@pytest.mark.demo_step(9)
async def test_step09_audit_log_query(
    control_client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    """REAL: GET /admin/audit returns the documented envelope.

    The endpoint is currently a stub (PR #81) returning `{entries: [], _stub: True}`.
    We assert the *contract* so that when the real implementation lands, the
    payload shape regression-fails loudly here instead of in console code.
    """
    # Arrange — fixtures supply client + headers.
    # Act
    try:
        r = await control_client.get("/admin/audit", headers=admin_headers, params={"limit": 50})
    except httpx.TransportError as e:
        # TransportError covers ConnectError AND ConnectTimeout/ReadTimeout etc.;
        # a slow-to-refuse control plane was raising ConnectTimeout and crashing
        # the test instead of skipping cleanly.
        pytest.skip(f"control plane not reachable at {control_client.base_url}: {e}")

    # Assert — contract: 200 + JSON with `entries` list and `limit` echo.
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "entries" in body, f"missing 'entries' key: {body}"
    assert isinstance(body["entries"], list)
    assert body.get("limit") == 50


# ---------------------------------------------------------------------------
# Step 10 — cross-tenant deny + VM cannot reach internet
# ---------------------------------------------------------------------------


@pytest.mark.demo_step(10)
@pytest.mark.hw_blocked
def test_step10_cross_tenant_and_egress_deny() -> None:
    """HW: requires real NSX DFW + at least two tenant VMs.

    Verifies (when hardware is wired):
      - tenant-B PC mounting `\\\\fs\\u\\alice\\workspace` → connection refused / timeout
      - tenant-A VM `curl https://www.google.com` → blocked (no route / DFW deny)
    """
    pytest.skip("NSX DFW lab not ready (hw_blocked)")
    # Arrange — SMB client from tenant-B subnet; ssh into tenant-A VM
    # Act    — attempt mount; attempt curl
    # Assert — both fail with deny-shaped error
