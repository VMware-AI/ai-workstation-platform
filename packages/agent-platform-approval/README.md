# agent-platform-approval (C13)

User-request approval workflow: pending → approved | rejected, with per-step audit log.

## Public API

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from agent_platform_approval import ApprovalService, Base, State

engine = create_engine("postgresql+psycopg://...")
Base.metadata.create_all(engine)  # or wire into C1's Alembic env

with Session(engine) as s:
    svc = ApprovalService(s)
    req = svc.submit(requester="alice", package="agent-vm-small", justification="POC")
    s.commit()

    # admin side
    svc.approve(req.id, admin="bob", reason="quota available")
    # or:  svc.reject(req.id, admin="bob", reason="quota exceeded")
    s.commit()
```

## State machine

```
                 ┌──────────────┐
                 │   pending    │  ◄── only entry state
                 └──────┬───────┘
              approve   │   reject
                  ┌─────┴─────┐
                  ▼           ▼
          ┌──────────┐  ┌──────────┐
          │ approved │  │ rejected │   ◄── terminal
          └──────────┘  └──────────┘
```

`approved` and `rejected` are terminal — no reopens, no soft-deletes. If a
decision needs to change, create a new request that references the old id.

## Tables

- `approval_requests` — one row per request
- `approval_audit_events` — every state transition + free-form admin comments

## HTTP surface (optional)

```python
from fastapi import FastAPI
from agent_platform_approval.http import build_router

app = FastAPI()
app.include_router(build_router(get_session))   # caller supplies the session factory
```

Routes:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/approvals/requests` | submit |
| `GET`  | `/approvals/requests` | list (filter: `state`, `requester`, `limit`) |
| `GET`  | `/approvals/requests/{id}` | get one (includes audit history) |
| `POST` | `/approvals/requests/{id}/approve` | approve (optional reason) |
| `POST` | `/approvals/requests/{id}/reject` | reject (reason required → 422 otherwise) |
| `POST` | `/approvals/requests/{id}/comment` | attach note without state change |

Install with the optional extra:

```bash
pip install agent-platform-approval[http]
```

## Tests

```bash
cd packages/agent-platform-approval
pytest                              # 31 tests: unit + HTTP TestClient
```

## Scope (M1)

- ✅ 1.17.1 state machine + DB schema (this package)
- ✅ 1.17.1.2 HTTP router (this package, optional extra)
- ⬜ 1.17.2 admin UI (C2 — calls these endpoints)
- ⬜ 1.17.3 trigger 1.11 single-user provisioning on approve (control-plane)
- ⬜ 1.17.4 email / IM notifications (C17)
- ✅ 1.17.5 audit log — every transition writes one `AuditEvent` in the same TX
