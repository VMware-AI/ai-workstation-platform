# =============================================================================
# Packer template: agent-platform-base
# AI Workstation Platform — C3 Gold Image
#
# Usage:
#   packer init .
#   packer build -var-file=./var-secrets.pkrvars.hcl .
#
# Prerequisites:
#   - hashicorp/packer (>= 1.10)
#   - hashicorp/vsphere plugin: packer plugins install github.com/hashicorp/vsphere hashicorp/packer
# =============================================================================

packer {
  required_version = ">= 1.10"

  required_plugins {
    vsphere = {
      version = ">= 1.4.0"
      source  = "github.com/hashicorp/vsphere"
    }
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Source: vsphere-clone
# Clones from an existing vSphere VM template — no ISO download needed.
# ─────────────────────────────────────────────────────────────────────────────

source "vsphere-clone" "ubuntu2204" {
  # ── Connection ────────────────────────────────────────────────────────────
  vcenter_server      = var.vcenter_server
  username            = var.vcenter_username
  password            = var.vcenter_password
  insecure_connection = var.vcenter_insecure

  # ── Clone source ───────────────────────────────────────────────────────────
  datacenter           = var.vcenter_datacenter
  template             = var.source_template
  vm_folder            = var.vm_folder
  resource_pool        = var.vcenter_resource_pool
  datastore            = var.datastore
  storage_policy       = var.storage_policy

  # ── Hardware ───────────────────────────────────────────────────────────────
  vm_guest_id          = var.vm_guest_id
  disk_size            = var.disk_size
  disk_controller_type = var.disk_controller_type
  memory               = var.memory
  cpu                  = var.cpu
  nested_hypervisor    = var.nested_hypervisor

  # ── Network ────────────────────────────────────────────────────────────────
  network_card             = var.network_card
  network_adapter_type     = var.network_adapter_type
  network                 = var.network

  # ── Boot / shutdown ────────────────────────────────────────────────────────
  boot_wait      = var.boot_wait
  shutdown_command = var.shutdown_command
  shutdown_timeout = var.shutdown_timeout

  # ── cloud-init ─────────────────────────────────────────────────────────────
  # Packer's built-in cloud-init support injects config into the VM via VMware
  # guestinfo. The OVF environment mechanism (via /usr/share/oem/ds-identify
  # or cloud-init datasources) then picks it up on first boot.
  cloud_init          = local.cloud_init_data
  cloud_init_filename = "user-data"

  # ── communicator (SSH) ─────────────────────────────────────────────────────
  communicator = "ssh"
  ssh_username = "ubuntu"
  # SSH key is injected via cloud-init ssh_authorized_keys.
  # Disable password auth and sudo-without-tty for security.
  ssh_handshake_attempts = 20
  ssh_timeout            = "15m"
}

# ─────────────────────────────────────────────────────────────────────────────
# Source: vsphere-iso (alternative — downloads ISO each build)
# Uncomment this and comment the vsphere-clone source above to build from ISO.
# ─────────────────────────────────────────────────────────────────────────────

# source "vsphere-iso" "ubuntu2204" {
#   vcenter_server      = var.vcenter_server
#   username            = var.vcenter_username
#   password            = var.vcenter_password
#   insecure_connection = var.vcenter_insecure
#
#   datacenter           = var.vcenter_datacenter
#   datastore            = var.datastore
#   vm_folder            = var.vm_folder
#   resource_pool        = var.vcenter_resource_pool
#   storage_policy       = var.storage_policy
#
#   vm_guest_id    = var.vm_guest_id
#   disk_size      = var.disk_size
#   disk_controller_type = var.disk_controller_type
#   memory         = var.memory
#   cpu            = var.cpu
#   nested_hypervisor = var.nested_hypervisor
#
#   network                 = var.network
#   network_card            = var.network_card
#   network_adapter_type    = var.network_adapter_type
#
#   boot_wait     = var.boot_wait
#   shutdown_command = var.shutdown_command
#   shutdown_timeout = var.shutdown_timeout
#
#   iso_url           = "https://releases.ubuntu.com/22.04/ubuntu-22.04-live-server-amd64.iso"
#   iso_checksum      = "sha256:10fcfc1a3e4b3e5e7c6d8b9a0f1e2d3c4b5a6f7e8d9c0b1a2f3e4d5c6b7a8f"
#   guest_os_type     = "ubuntu64Guest"
#
#   # cloud-init via http directory
#   boot_command       = [
#     "<esc><wait>",
#     "<esc><wait>",
#     "<enter><wait>",
#     "/install/vmware/tools/identity.<wait>",
#     "autoinstall ds=nocloud-net;s=http://{{ .HTTPIP }}:{{ .HTTPPort }}/<wait>",
#     "<enter><wait>"
#   ]
#   http_directory      = "./http"
#   ssh_username        = "ubuntu"
#   ssh_handshake_attempts = 20
#   ssh_timeout         = "15m"
# }

# ─────────────────────────────────────────────────────────────────────────────
# Build definition
# ─────────────────────────────────────────────────────────────────────────────

build {
  name = "agent-platform-base"

  sources = [
    # "source.vsphere-iso.ubuntu2204"   # uncomment for ISO-based build
    "source.vsphere-clone.ubuntu2204", # default: clone from template
  ]

  # ── Provisioner: wait for cloud-init to finish ─────────────────────────────
  # cloud-init runs as a systemd service. We wait until /run/cloud-init/result
  # exists before running application setup to avoid races.
  provisioner "shell" {
    execute_command = "sudo {{ .Vars }} {{ .Path }}"
    environment_vars = ["DEBIAN_FRONTEND=noninteractive"]
    script = "./scripts/00-wait-cloud-init.sh"
  }

  # ── Provisioner: base OS hardening ───────────────────────────────────────
  provisioner "shell" {
    execute_command = "sudo {{ .Vars }} {{ .Path }}"
    environment_vars = ["DEBIAN_FRONTEND=noninteractive"]
    script = "./scripts/01-base-hardening.sh"
  }

  # ── Provisioner: install core toolchain ───────────────────────────────────
  provisioner "shell" {
    execute_command = "sudo {{ .Vars }} {{ .Path }}"
    environment_vars = ["DEBIAN_FRONTEND=noninteractive"]
    script = "./scripts/02-install-toolchain.sh"
  }

  # ── Provisioner: install AI agents (qcoder / Goose / Claude Code) ─────────
  # Skipped if agent_install_enabled=false
  provisioner "shell" {
    only   = ["vsphere-clone.ubuntu2204"]
    script = "./scripts/03-install-agents.sh"
  }

  # ── Provisioner: agent-platform platform component bootstrap ─────────────────────
  # Pulls agent-platform-control, agent-platform-agent-adapter, etc. from C4 agent-platform-repo.
  # Runs ONLY when agent_platform_repo_url is provided.
  provisioner "shell" {
    except  = ["vsphere-iso.ubuntu2204"]
    script  = "./scripts/04-bootstrap-platform.sh"
  }

  # ── Provisioner: verification ──────────────────────────────────────────────
  provisioner "shell" {
    execute_command = "{{ .Vars }} {{ .Path }}"
    script    = "./scripts/99-verify.sh"
  }

  # ── Post-processor: compact disk (zero-fill thin provisoned blocks) ────────
  # Run via VMware Tools inside the VM before shutdown.
  # Note: for vSAN this is a no-op; vSAN deduplicates+compresses automatically.
  # Uncomment for traditional datastore:
  #
  # post-processor "shell-local" {
  #   inline = ["echo 'Disk compact not needed on vSAN'"
  # }
}

# ─────────────────────────────────────────────────────────────────────────────
# Variable: cloud-init user-data content
# Injected via vsphere-clone's cloud_init parameter.
# We pass it as a heredoc string rather than a file path so that it can be
# version-controlled without leaking secrets (no IPs / passwords here).
# ─────────────────────────────────────────────────────────────────────────────

locals {
  build_timestamp = formatdate("YYYYMMDD", timestamp())
  output_ova      = "agent-platform-base-${formatdate("YYYYMMDD", timestamp())}.ova"

  cloud_init_data = templatefile("${path.root}/http/user-data", {
    ssh_authorized_keys = var.ssh_authorized_keys
    ntp_server          = var.ntp_server
    proxy_url           = var.http_proxy
  })
}
