# M1.26 E2E Failure Runbook

Goal (sub-task 1.26.3): when an e2e step fails, the on-call engineer should
locate the root cause in **≤ 5 minutes** using only this runbook + the test
log + the audit DB.

For each step, the table below lists the first three things to check, in
order. Stop at the first that explains the failure.

## How to read a failure

```
pytest tests/test_demo_step01_to_step10.py::test_step07_token_dashboard_renders -vv
```

The test's docstring states REAL / HW / WAIT — if it's HW or WAIT and you
got an `AssertionError` instead of a skip, the fixture lied (probably the
`hw_blocked` skip mechanism is broken in conftest).

## Per-step triage

| Step | Failure | Check 1 | Check 2 | Check 3 |
|------|---------|---------|---------|---------|
| 01   | pack failed | `which agent-platform-bundle` | `uv sync` at repo root | check zstd lib installed (`python -c "import zstandard"`) |
| 01   | sha256 wrong length | bundle CLI bug | re-run with `AGENT_PLATFORM_BUNDLE_DEBUG=1` | open issue against C9 |
| 02   | timeout to ready | provisioner reachable? `vcsim` running? | `/v1/deployments/{id}` last_error column | C1 orchestrator worker logs |
| 03   | circle <3 dots | console pulling stale data — hard refresh | C1 `/api/topology/tenant/A` returns 3? | NSX SG actually populated? `vmware-nsx sg_get tenant-A-vms` |
| 03   | SG members != 3 | C14 pool-scheduler NSX adapter logs | NSX manager itself: `vmware-nsx group_get tenant-A-vms` | quota — too many SGs on NSX |
| 04   | mount refused | DNS for `fs.agent-platform.local` resolves? | tenant routing reaches fileshare VLAN? | Samba/vSAN service status |
| 04   | write fails | quota? `df -h` inside fileshare VM | ACL — Alice in posix group? | SMB protocol mismatch (SMB1 disabled) |
| 05   | terminal blank | portal SSO token expired? | ttyd backend reachable from portal? | container SSH key not pushed |
| 06   | summary missing | agent run actually finished? check `~/.agent/last-run.log` in VM | LLM gateway returning 200? `curl $AGENT_PLATFORM_GATEWAY/health` | GPU OOM? `nvidia-smi` |
| 07   | KPI absent | console build stale — rebuild | C5 gateway not emitting token records | console expects different JSON shape than C5 emits |
| 08   | upgrade stuck | C1 upgrade endpoint returned 5xx | new image not pulled — registry creds | data-volume detach/reattach failed |
| 09   | endpoint 401 | admin token rotated — refresh `AGENT_PLATFORM_ADMIN_TOKEN` | RBAC role missing | C1 auth middleware regression |
| 09   | shape mismatch | `_stub` flag flipped to False but field renamed | check C1 PR diff for /admin/audit | revert breaking change or fix this test |
| 10   | cross-tenant SUCCEEDS | DFW policy not applied — `vmware-nsx-security dfw_rule_list` | rule order wrong (allow before deny) | NSX cluster split-brain |

## Escalation

- Test infrastructure broken (fixtures, conftest) → @zw
- C1 / control plane → @c1-team in repo CODEOWNERS
- C2 console → @c2-team
- NSX / vSAN / GPU → cluster ops Slack #agent-platform-cluster

If unsure, attach `pytest -vv --tb=long` output + the audit DB query result
for the same 5-minute window:

```
sqlite3 ~/.vmware/audit.db \
  "SELECT * FROM audit_log WHERE ts > strftime('%s','now')-300 ORDER BY ts DESC"
```
