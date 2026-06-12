# agent-platform-web-ui (C21)

> Unified web UI + control plane for the AI Workstation Platform.
> Next.js (full-stack) + Prisma/Postgres + BullMQ/Redis.

This package is the platform's **single web UI**, converging the admin console
(C2) and control-plane responsibilities (C1/C13/C14) per
[doc 32](../../docs/architecture/32-2026-06-01-web-ui-convergence.md).
Product core = **deliver agent VMs via vCenter**; the end-user surface is the
agent's own web UI inside the delivered VM (see
[doc 31](../../docs/architecture/31-2026-06-01-user-interaction-model-and-portal-removal.md)),
not this app.

## Status — converging (phased, doc 32 §8)

- **Phase 0 (this)**: renamed the package to `agent-platform-web-ui`, registered as a component.
- **Phase 1**: clear security blockers (#91 11 HIGH + #90 24 Major) before any prod use.
- **Phase 2**: cut the docker-container-agent stack (`lib/runtime.ts`,
  `providers/docker`, `/api/instances/[id]/invoke`); wire real vCenter
  provisioning (P-2: call C1's pyVmomi provisioner; P-1: TS rewrite — doc 32 §4).
- **Phase 3**: distill C2 console pages (vCenter inventory / deployments /
  approvals / token usage) into here.
- **Phase 4**: retire C1/C2/C13/C14.

⚠️ Not production-ready: open security blockers (#86/#90/#91) and the vSphere
provisioner is still a stub (#90 M14). Do not deploy to a customer until Phase 1+2.

## Run locally (dev)

Requires Docker (Postgres + Redis), Node 20+, npm.

```bash
cd packages/agent-platform-web-ui
cp .env.example .env            # set POSTGRES_PASSWORD, DATABASE_URL, etc.

# Postgres + Redis
docker compose up -d postgres redis

# deps + schema
npm install
npx prisma migrate deploy && npx prisma generate
npx prisma db seed              # seeds a demo account (see below)

# dev server (http://localhost:3000)
npm run dev
```

Provisioning is async: the deploy API only enqueues — the **worker** is what
calls govc, so **without it deploys sit in PENDING forever**. Run it as a
self-healing container (recommended) or a host process:

```bash
docker compose up -d --build provisioner-worker   # restart: unless-stopped, govc baked in
# or, for worker-code debugging: npm run worker    # host process, dies with the shell
```

`start.sh` brings up Postgres + Redis + the worker container + dev (`:3000`) in
one go. See [SETUP.md §3](./SETUP.md) for the worker-keepalive tradeoffs.

**Already running an older checkout?** Upgrade with `git pull && npm install &&
./start.sh` — see [SETUP.md §6 升级](./SETUP.md) for what that re-runs (migrations,
worker image rebuild) and the gotchas.

### First login

The seed creates a demo account you can log in with at `/login`:

- **email:** `demo@local.test`
- **password:** `demo123456`  (tenant `demo`, role OWNER)

The seed is idempotent (`npm run seed` re-runs it and resets the demo password).
Or create your own account at `/register`. If `/login` says "邮箱或密码错误", the
database hasn't been seeded yet — run `npx prisma db seed`.

### Accessing from another machine (LAN)

`npm run dev` already binds `0.0.0.0`, but Next blocks dev-server requests from
origins it doesn't recognize. To open the app from another machine at
`http://<server-ip>:3000`, set the access host in `.env` and restart:

```bash
DEV_ALLOWED_ORIGINS=172.20.20.6        # the IP you type in the browser, no scheme
```

Symptom if unset: requests fail / login won't submit from the remote machine
(works fine on the server's own `localhost`). Dev-only — no effect on prod builds.

## Layout

```
src/app/            Next.js app router — (auth) + (dashboard) pages + /api routes
src/lib/            auth, tenant, prisma, queue, providers (vsphere)
worker/             BullMQ worker (provision / reaper jobs)
prisma/             schema + migrations
```

## Scripts

`npm run dev | build | start | lint | test | worker | seed`

See [doc 32](../../docs/architecture/32-2026-06-01-web-ui-convergence.md)
for the convergence plan, keep/cut table, and security gate.
