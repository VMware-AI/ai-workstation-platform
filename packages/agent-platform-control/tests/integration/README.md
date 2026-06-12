# E2E Smoke Cassette (PR-H)

In-process software E2E that mirrors `docs/architecture/acceptance/m1.md`.

## What it is

`test_e2e_software.py` runs the M1 acceptance flow against:

- **C1 control plane** via `ASGITransport` (no network, no uvicorn)
- **`FakeProvisioner`** (no vCenter, no vcsim)
- **In-process `DeploymentWorker.drain_once()`** (no scheduler)

Total runtime: **< 2 seconds**. Designed for CI.

## What it tests today (PR-H)

| M1 step | Status | Notes |
|---|---|---|
| A1 submit → 201 | ✅ | admin POST `/v1/deployments` |
| A2 approval-driven | ⏳ skipped | PR #129 from-approval |
| A3 worker drain → succeeded | ✅ | exercises FakeProvisioner |
| A3 partial failure | ✅ | item 001 keeps failing → `partially_failed` |
| A3 token columns populated | ✅ | guards against silent NULL bug |
| A4 heartbeat ingest | ⏳ skipped | PR #136 (PR-D) heartbeat endpoint |
| A5 admin re-read | ✅ | GET `/v1/deployments/{id}` |
| R1 unhealthy at 5min | ⏳ skipped | PR #136 sweeper |
| R2 lost at 30min | ⏳ skipped | PR #136 sweeper |
| R3 failed-cleanup at 24h | ⏳ skipped | PR #136 cleanup_cron |
| R4 24h uptime | n/a | prod-only |
| R5 RSS stability | n/a | prod-only |

Each `@pytest.mark.skip` has a TODO marker keyed to the blocking PR — grep for `TODO PR #` to see them all.

## Seed contract (issue #163)

`smoke_seed` must register **every** entity the A1 payload references, or
the flow fails late with a 404/422 instead of a fixture error. Since PR-E
(#137, decision 12) `POST /v1/deployments` calls
`_verify_image_version_or_fail`, so the seed must include an `ImageVersion`
row whose `version` matches `deployment_payload`'s `image_version`
(`DEFAULT_IMAGE_VERSION = "v0.1.0"`). It is seeded `signature_b64=None`
(legacy unsigned) so the verifier accepts it without a signing key. If you
change the payload's `image_version`, change the constant — both read it.

Regression history: a stricter image-version registry (post-#137) started
404-ing the smoke A1 because the seed predated the registry; #163 fixed it
by registering the version in `smoke_seed`.

## How to run

From the repo root:

```bash
make smoke
```

Or directly:

```bash
cd packages/agent-platform-control
PYTHONPATH="src:../agent-platform-approval/src" \
  uv run --no-project --with ... \
  pytest tests/integration/test_e2e_software.py -v
```

(See `Makefile` for the full `--with` list.)

## Failure output

Every assertion in `lib_smoke.py` and the test file ends with a teaching message: which step (A1/A3/A5), what we expected, what we got, what to check. Example:

```
A5 FAILED — deployment state='in_progress' after worker drain.
items: [{'id': 1, 'state': 'cloning', ...}, ...]
```

Tells you the deployment didn't finish draining — usually means a worker tick was missed or `FakeProvisioner` is sleeping.

## Adding to CI

Currently **not enabled** in CI. To enable after PR-A (#133) merges:

```yaml
- name: E2E software smoke
  run: make smoke
```

Holding until PR-A is on main so `DeploymentItem` schema matches the
PR-A `agent_user` + `template_path` fields the worker fills.

## When PRs merge

| PR merges | Cascade |
|---|---|
| #129 | un-skip `test_a2_approval_triggers_deployment` + write body |
| #136 (PR-D) | un-skip A4 + R1 + R2 + R3 |

Each un-skip is a separate small commit, not a single mass change — keep history clean.
