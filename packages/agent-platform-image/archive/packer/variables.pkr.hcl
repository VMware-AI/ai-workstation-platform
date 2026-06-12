# =============================================================================
# Packer Variables — agent-platform-image
# =============================================================================
# Note: computed values like build_timestamp / output_ova live in the locals
# block of agent-platform-base.pkr.hcl, because HCL2 disallows function calls or
# variable references inside `variable.default`.

# ── vSphere / vCenter ────────────────────────────────────────────────────────

variable "vcenter_server" {
  type      = string
  sensitive = true
  description = "vCenter FQDN, e.g. vc01.corp.local"
}

variable "vcenter_username" {
  type      = string
  sensitive = true
  description = "vCenter username, e.g. admin@vsphere.local"
}

variable "vcenter_password" {
  type      = string
  sensitive = true
  description = "vCenter password"
}

variable "vcenter_insecure" {
  type    = bool
  default = false
  description = "Skip TLS verification for vCenter (use only in air-gap lab)"
}

# ── Clone source ─────────────────────────────────────────────────────────────

variable "source_template" {
  type        = string
  default     = "ubuntu-2204-golden"
  description = "vSphere VM template to clone from"
}

variable "vm_folder" {
  type        = string
  default     = "Agent Platform"
  description = "vSphere folder to place built VMs"
}

# ── VM hardware ───────────────────────────────────────────────────────────────

variable "vm_guest_id" {
  type    = string
  default = "ubuntu64Guest"
}

variable "disk_size" {
  type    = number
  default = 25600
  description = "Disk size in MB (25 GB)"
}

variable "disk_controller_type" {
  type    = string
  default = "pvscsi"
}

variable "memory" {
  type    = number
  default = 4096
  description = "RAM in MB"
}

variable "cpu" {
  type    = number
  default = 2
  description = "Number of vCPUs"
}

variable "nested_hypervisor" {
  type    = bool
  default = true
  description = "Enable nested virtualization (required for vGPU / containers)"
}

# ── Network ───────────────────────────────────────────────────────────────────

variable "network" {
  type        = string
  default     = "sddc-seg"
  description = "Portgroup or segment name"
}

variable "network_card" {
  type    = string
  default = "vmxnet3"
}

variable "network_adapter_type" {
  type    = string
  default = "vmxnet3"
}

# ── Storage ───────────────────────────────────────────────────────────────────

variable "datastore" {
  type        = string
  default     = "vsanDatastore"
  description = "Datastore / storage policy"
}

variable "storage_policy" {
  type    = string
  default = ""
  description = "vSAN storage policy JSON (optional), e.g. {\"hostFailuresToTolerate\":1}"
}

# ── Build behavior ────────────────────────────────────────────────────────────

variable "boot_wait" {
  type    = string
  default = "10s"
}

variable "shutdown_command" {
  type    = string
  default = "sudo shutdown -h now"
}

variable "shutdown_timeout" {
  type    = string
  default = "10m"
}

# ── Artifact output ──────────────────────────────────────────────────────────
# output_ova is computed in locals (uses build_timestamp), see agent-platform-base.pkr.hcl.

variable "vcenter_datacenter" {
  type        = string
  default     = "SDDC-Datacenter"
  description = "vSphere datacenter name"
}

variable "vcenter_resource_pool" {
  type    = string
  default = ""
}

# ── cloud-init template inputs ───────────────────────────────────────────────
# Consumed by templatefile() against http/user-data.

variable "ssh_authorized_keys" {
  type        = list(string)
  default     = []
  description = "SSH public keys injected into the ubuntu user via cloud-init. At least one is required for production builds."
}

variable "ntp_server" {
  type        = string
  default     = ""
  description = "Optional NTP server FQDN. Empty falls back to pool.ntp.org."
}

variable "http_proxy" {
  type        = string
  default     = ""
  description = "Optional HTTP proxy URL for the cloud-init bootstrap phase. Empty disables proxy."
}
