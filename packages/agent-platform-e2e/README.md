# agent-platform-e2e (C-E2E)

End-to-end acceptance test framework for the M1.26 10-step demo.

> Source: `docs/plans/2026-05-17-agent-platform-design.md` §7
> Tasks: 1.26.1 (pytest scaffold), 1.26.2 (record), 1.26.3 (runbook), 1.26.4 (cross-tenant deny)

## Why this package exists

The 10-step demo is the M1 milestone gate. Until we have CI driving every
step end-to-end against either a real cluster or a stub, "demo works" is
folklore. This package turns the demo into pytest so it can run in three
modes:

| Mode | Command | What runs |
|------|---------|-----------|
| Laptop (default) | `uv run pytest -m "not hw_blocked"` | step 01 (REAL bundle pack), step 07 (REAL console nav, skips if console not up), step 09 (REAL audit contract, skips if control not up); rest skip with a concrete WAIT pointer |
| Staging (no hw) | same, with `AGENT_PLATFORM_*_URL` set to staging | real assertions against staging C1/C2/C12 |
| Real cluster | `AGENT_PLATFORM_HW_AVAILABLE=1 uv run pytest` | all 10 steps run; HW-gated steps no longer auto-skip |

## Local quickstart

```bash
cd packages/agent-platform-e2e
uv sync
uv run playwright install chromium    # only needed for step 07 (console UI)
uv run pytest --collect-only          # should list 10 tests
uv run pytest -m "not hw_blocked" -vv # green or skipped, never failed
```

Lint + security:

```bash
uv run ruff check .
uv run ruff format --check .
uvx bandit -r tests/ src/
```

## Environment variables

| Variable | Default | Used by |
|----------|---------|---------|
| `AGENT_PLATFORM_CONTROL_URL` | `http://localhost:8000` | step 09 audit query |
| `AGENT_PLATFORM_CONSOLE_URL` | `http://localhost:5173` | step 07 token dashboard |
| `AGENT_PLATFORM_PORTAL_URL` | `http://localhost:5174` | step 05 web terminal |
| `AGENT_PLATFORM_FILESHARE_UNC` | `\\fs.agent-platform.local\u\alice` | steps 04 / 06 / 10 |
| `AGENT_PLATFORM_ADMIN_TOKEN` | `dev-admin-token-CHANGE-ME` | every admin call |
| `AGENT_PLATFORM_ALICE_TOKEN` | `dev-alice-token` | every Alice call |
| `AGENT_PLATFORM_HW_AVAILABLE` | `0` | gates `hw_blocked` marker |

## What each step is wired to today

| Step | State today | Becomes REAL when |
|------|-------------|-------------------|
| 01 scale bundle pack + sha256 | REAL (uses C9 CLI offline) | C1 upload endpoint lands |
| 02 batch create 3 VMs | WAIT (#103 vcsim) | vcsim provisioner merges |
| 03 console circle + NSX SG | HW | NSX lab + tenant-A SG present |
| 04 SMB workspace + outbox | HW | vSAN File Services provisioned |
| 05 portal web terminal | WAIT (1.20/1.22) | portal SSO + ttyd land |
| 06 inbox/summary.md ≤ 30s | HW | GPU + vLLM + agent runtime |
| 07 token dashboard | REAL (playwright, auto-skip if console down) | console exposes route |
| 08 blue-green upgrade | WAIT (1.11) | upgrade API lands |
| 09 audit log query | REAL (asserts C1 stub envelope contract) | C1 audit impl lands (test stays valid) |
| 10 cross-tenant deny | HW | NSX DFW lab |

## CI integration

This package is intentionally **not** wired into the root `pytest` collection
on this PR — it adds a heavyweight dependency (playwright + browsers) that
not every contributor needs. A follow-up PR adds a dedicated GitHub Actions
job that runs `uv run pytest -m "not hw_blocked"` after every `main` push,
plus a nightly job that runs everything against staging.

## Files

```
packages/agent-platform-e2e/
├── pyproject.toml
├── README.md
├── src/agent_platform_e2e/
│   ├── __init__.py
│   ├── env.py            # frozen dataclass — env vars → test config
│   └── record.py         # `agent-platform-e2e-record` stub (task 1.26.2)
├── scripts/
│   └── record_demo.py    # convenience entry point
└── tests/
    ├── conftest.py                          # fixtures (env, http, playwright)
    ├── runbook_on_failure.md                # 5-minute triage table (1.26.3)
    └── test_demo_step01_to_step10.py        # 10 tests, one per demo step
```

## Failure triage

See [tests/runbook_on_failure.md](tests/runbook_on_failure.md) — per-step
"first three things to check" table targeting ≤ 5-minute MTTD.

## License

Proprietary (TBD) — matches monorepo.
