"""Pydantic settings — read from env or `.env`. Never commit a real .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Committed dev defaults that must never survive into a production posture.
_DEV_ADMIN_TOKEN = "dev-admin-token-CHANGE-ME"  # noqa: S105  # dev sentinel; prod boot refuses it  # nosec B105 — dev sentinel; prod boot refuses it
_DEV_INGEST_TOKEN = "dev-ingest-token-CHANGE-ME"  # noqa: S105  # dev sentinel; prod boot refuses it  # nosec B105 — dev sentinel; prod boot refuses it
_DEV_FERNET_KEY = "YWdlbnQtcGxhdGZvcm0tZGV2LWtleS0zMmJ5dGVzISE="


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AGENT_PLATFORM_", extra="ignore")

    # === DB ===
    # Default to SQLite for dev/test; prod overrides to postgresql+asyncpg://...
    database_url: str = "sqlite+aiosqlite:///./agent-platform-control.db"

    # === Auth ===
    # M1 stub: hand-coded admin token in env. M1.2.3 wires Keycloak.
    admin_api_token: str = Field(default="dev-admin-token-CHANGE-ME")
    # SEC-2: secure by default. False means the committed dev secrets trip the
    # startup fail-fast (production_safety_problems) and the X-User fake-auth
    # path is off — so a deploy that simply forgot to configure auth refuses to
    # boot rather than silently trusting the committed default admin token.
    # Dev/test opt IN explicitly via AGENT_PLATFORM_ENABLE_FAKE_AUTH=1
    # (the test suite sets it in conftest; local dev sets it in .env).
    enable_fake_auth: bool = False
    deployment_token_fernet_key: str = Field(
        default="YWdlbnQtcGxhdGZvcm0tZGV2LWtleS0zMmJ5dGVzISE="
    )  # dev/test only; prod must source from C18/Vaultwarden

    # === Service-to-service ===
    # Shared bearer token C5 (gateway) presents to POST /api/ingest/token-usage.
    # Distinct from admin_api_token so gateway compromise doesn't grant admin.
    c1_ingest_service_token: str = Field(default="dev-ingest-token-CHANGE-ME")

    # === Secrets backend (C18 Vaultwarden, harness H-11 #212) ===
    # When vaultwarden_url is set, secret-bearing fields may use
    # ``vault://<folder>/<item>`` references — resolved at startup via
    # agent-platform-secrets. ``env://VAR`` and plain literals always work.
    # Empty URL keeps the dev/.env posture (EnvSecretResolver, no network).
    vaultwarden_url: str = ""
    vaultwarden_client_id: str = ""
    vaultwarden_client_secret: str = Field(default="", repr=False)
    vaultwarden_timeout_s: float = 10.0

    # === Approval-triggered provisioning (Task 1.17.3) ===
    # An approved C13 request carries only requester + package; the tenant and
    # image version are not part of the approval. M1 is single-tenant, so the
    # from-approval bridge fills both from these defaults. Multi-tenant (post-M1)
    # will move tenant resolution onto the request itself.
    default_tenant_id: str = "default"
    default_image_version: str = "v0.1.0"

    # === Cleanup cron (decision 5 PR-D) ===
    # Failed VMs stay around for this many hours so admins can inspect cloud-
    # init logs / vCenter console before the cron destroys them. 24h is the
    # decision-5 default; customer ops can extend per their runbook.
    failed_vm_retain_hours: int = 24
    # Cleanup cron disabled by default so M1 demo doesn't accidentally chew
    # through a tenant's quota. Set true once the customer runbook covers
    # 'how to recover before the 24h window'.
    cleanup_cron_enabled: bool = False

    # === Image signing (decision 12 PR-E) ===
    # PEM-encoded SubjectPublicKeyInfo (RSA / ECDSA / Ed25519) used to verify
    # image_version signatures at registration + clone time. Empty default
    # disables verification (M1 dev mode) — clones still work but log a
    # warning. Prod must set this via AGENT_PLATFORM_IMAGE_SIGNING_PUBKEY_PEM.
    image_signing_pubkey_pem: str = ""

    # === App ===
    app_name: str = "agent-platform-control"
    log_level: str = "INFO"
    # PR-F F-3: JSON log output for log shippers. Default ON since H-12
    # (#213) — structured logs with request_id are the cross-service
    # correlation baseline. Set AGENT_PLATFORM_LOG_JSON=0 for human-readable
    # dev output.
    log_json: bool = True

    # === Worker (PR-F F-1) ===
    # Decision 18 PR-F: DeploymentWorker is opt-in. Set both fields below to
    # explicitly start a worker — leaving provisioner_kind="" prevents a
    # dev-only FakeProvisioner from sneaking into prod by accident.
    enable_worker: bool = False
    provisioner_kind: str = ""  # "" | "fake" | "vmware" (vmware = follow-up)
    # Worker construction params — wired only when enable_worker=True.
    worker_registry_url: str = "registry.example.invalid/agent-platform"
    worker_goose_image_tag: str = "1.34.1"
    worker_litellm_gateway_url: str = "http://agent-platform-llm-gateway.invalid:4000"
    worker_heartbeat_url: str = "http://agent-platform-control.invalid/v1/heartbeat"
    # SEC-1: trusted-network CIDR opened to the in-VM ttyd terminal (port 7681).
    # Empty by default → install-agent.sh fails closed and leaves 7681
    # firewalled. Set to the portal/control-plane NSX segment (e.g. 10.20.0.0/16)
    # to enable the Terminal page. Real fix is the M2 W-3.3 HMAC token sidecar.
    worker_ttyd_allow_cidr: str = ""
    worker_poll_interval_s: float = 1.0

    # === vCenter (PR-F follow-up — runtime.py vmware factory wiring) ===
    # All required when provisioner_kind="vmware". Set via env or .env file:
    #   AGENT_PLATFORM_VSPHERE_URL=https://10.x.x.x/sdk
    #   AGENT_PLATFORM_VSPHERE_USER=svc-agent-platform@vsphere.local
    #   AGENT_PLATFORM_VSPHERE_PASSWORD=...
    #   AGENT_PLATFORM_VSPHERE_TEMPLATE=/path/to/cloud-init-template.yaml
    #   AGENT_PLATFORM_VSPHERE_VERIFY_SSL=false  # for self-signed certs only
    vsphere_url: str = ""
    vsphere_user: str = ""
    vsphere_password: str = Field(default="", repr=False)  # repr=False keeps it out of __repr__
    vsphere_template: str = ""  # path to cloud-init userdata template (jinja-style or static yaml)
    vsphere_verify_ssl: bool = True
    vsphere_clone_timeout_s: float = 600.0

    # === Fileshare (M1 single fileshare server; M2 per-tenant override) ===
    # When set, /api/me/instances annotates each agent with a UNC path
    # `\\<fileshare_base>\u\<owner>\workspace`. Empty default keeps the
    # portal showing "—" so we don't promise a path that doesn't resolve.
    fileshare_base: str = ""

    # === Quota (M1 soft default; M2.2 wires real C5 limits) ===
    # Static token budget per user per period. Surfaced via /api/me/usage
    # so the portal Quota badge has a non-fake numerator/denominator.
    quota_total_tokens: int = 1_000_000

    # === ttyd (W-3.1 mock → W-3.2 real direct wss) ===
    # When ``ttyd_real_mode`` is true, /api/me/instances/{vm_id}/ttyd-url
    # returns ``wss://{vm.ip_address}:{ttyd_port}/ws`` — the portal connects
    # directly to the VM's ttyd unit (cloud-init installs it; see
    # packages/agent-platform-image/cloud-init/scripts/install-agent.sh and
    # docs/architecture/29 §3). M1 trusts NSX segment isolation (no token);
    # W-3.3 follow-up will add an HMAC token sidecar in front of ttyd.
    #
    # When ``ttyd_real_mode`` is false (default), the endpoint falls back to
    # ``ttyd_mock_url`` — used for local demoing against
    # ``docker run tsl0922/ttyd`` without standing up real VMs. Empty
    # ``ttyd_mock_url`` → 503 with a teaching error rather than pretending
    # success.
    ttyd_real_mode: bool = False
    ttyd_port: int = 7681
    ttyd_mock_url: str = ""

    def production_safety_problems(self) -> list[str]:
        """List insecure committed-default secrets for a production posture.

        Only enforced when ``enable_fake_auth`` is False — the explicit signal
        that real auth is wired and the service faces real callers. Dev and
        test keep the committed defaults + fake auth and are unaffected
        (PR-review #57 admin token / #81 Fernet key — no startup fail-fast).
        """
        if self.enable_fake_auth:
            return []
        problems: list[str] = []
        if self.admin_api_token == _DEV_ADMIN_TOKEN:
            problems.append("AGENT_PLATFORM_ADMIN_API_TOKEN is the committed dev default")
        if self.c1_ingest_service_token == _DEV_INGEST_TOKEN:
            problems.append("AGENT_PLATFORM_C1_INGEST_SERVICE_TOKEN is the committed dev default")
        if self.deployment_token_fernet_key == _DEV_FERNET_KEY:
            problems.append(
                "AGENT_PLATFORM_DEPLOYMENT_TOKEN_FERNET_KEY is the committed dev default"
            )
        return problems


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Resolve env:// / vault:// secret references once at first access (app
    # startup). Pure-literal settings (dev default) pass through unchanged;
    # an unreachable vault refuses startup with a teaching error (H-11 #212).
    from .secrets_bootstrap import resolve_secret_settings

    return resolve_secret_settings(Settings())


def get_settings_fresh() -> Settings:
    """Re-read settings uncached so .env edits / runtime env changes are picked
    up. The single ``vsphere_*`` source of truth shared by the provisioner
    (runtime) and the admin diagnostic endpoints."""
    return Settings()
