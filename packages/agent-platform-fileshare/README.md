# agent-platform-fileshare

> C19 — vSAN File Services SMB share provider for AI Workstation Platform.

## Status

🔨 In progress — see [M0.7 PoC Plan](../../docs/plans/m0/0.7-fileshare-poc-plan.md) and GitHub Issues `component:C19`.

## Architecture

```
vSAN distributed storage → vSAN File Services (FSVM SMB endpoint)
                          ↑ per-user quota via Storage Policy
                          ↑ AD Kerberos/LDAP authentication
                          ↑ HA via FSVM failover
                          ↑ vSAN snapshots for recovery
```

**Tech Stack:** vSphere 8.0 U3+ · vSAN File Services · Active Directory · SMB 3.x · macOS / Windows / Linux clients

## Key Specs

| Capability | Implementation |
|---|---|
| Storage backend | vSAN (distributed, multi-replica) |
| File share | vSAN FSVM built-in SMB/NFS |
| Authentication | Active Directory (Kerberos + LDAP) |
| Per-user quota | vSAN Storage Policy |
| HA | FSVM automatic failover |
| Snapshot/recovery | vSAN snapshots via VSF |

## Local dev

See `poc/fileshare-vsan/README.md` for PoC setup. Production implementation follows [0.7-fileshare-poc-plan.md](../../docs/plans/m0/0.7-fileshare-poc-plan.md).
