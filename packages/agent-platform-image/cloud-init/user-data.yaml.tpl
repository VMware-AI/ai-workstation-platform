#cloud-config
# Agent Platform per-user VM cloud-init.
#
# Placeholders are substituted by C1 agent-platform-control at clone time via the
# VMware GuestInfo datasource (extraConfig.guestinfo.userdata).
#
# Required placeholders:
#   {{ AGENT_KIND }}             plugin selector ("goose" | "qoder" | "xiaoguai" | ...)
#   {{ AGENT_VERSION }}          plugin-specific version (e.g. docker tag)
#   {{ AGENT_USER }}             linux account name on the guest (= sanitized owner_id)
#   {{ AGENT_USER_UID }}         linux UID (deterministic 1000..50999)
#   {{ AGENT_REGISTRY_URL }}     where to pull the agent binary / image from
#   {{ GATEWAY_URL }}            LLM gateway base URL
#   {{ AGENT_PLATFORM_USER_TOKEN }}     one-shot bootstrap token for /api/cloud-init/exchange-token
#   {{ HEARTBEAT_URL }}          where the in-VM heartbeat reports to
#
# Optional placeholders (rendered empty when unset — fail closed):
#   {{ AGENT_MODEL }}            LLM model selection hint
#   {{ TTYD_ALLOW_CIDR }}        SEC-1 trusted-network CIDR for ttyd port 7681

users:
  - name: {{ AGENT_USER }}
    uid: "{{ AGENT_USER_UID }}"
    shell: /bin/bash
    create_home: true
    lock_passwd: true
    sudo: false

write_files:
  - path: /etc/agent-platform/install.env
    permissions: "0600"
    owner: root:root
    content: |
      AGENT_KIND={{ AGENT_KIND }}
      AGENT_VERSION={{ AGENT_VERSION }}
      AGENT_USER={{ AGENT_USER }}
      AGENT_USER_UID={{ AGENT_USER_UID }}
      AGENT_REGISTRY_URL={{ AGENT_REGISTRY_URL }}
      AGENT_MODEL={{ AGENT_MODEL }}
      GATEWAY_URL={{ GATEWAY_URL }}
      AGENT_PLATFORM_USER_TOKEN={{ AGENT_PLATFORM_USER_TOKEN }}
      HEARTBEAT_URL={{ HEARTBEAT_URL }}
      AGENT_RUNTIME_ENV=/etc/agent-platform/{{ AGENT_KIND }}.env
      # TTYD_ALLOW_CIDR (optional, SEC-1): the portal/control-plane segment CIDR
      # (e.g. 10.20.0.0/16) that install-agent.sh opens ttyd port 7681 to. It is
      # rendered empty when the deploy did not supply one, which keeps ttyd
      # firewalled (fail closed) — no remote terminal, but never an
      # unauthenticated shell exposed to the routable network. The real fix
      # (M2 W-3.3) puts an HMAC token sidecar in front of ttyd.
      TTYD_ALLOW_CIDR={{ TTYD_ALLOW_CIDR }}

runcmd:
  - [/opt/agent-platform/cloud-init/scripts/install-agent.sh]
