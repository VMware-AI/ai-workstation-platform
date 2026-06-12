import { sha512Crypt } from "./sha512-crypt";
import { encodeRuncmd, yamlField } from "./yaml-safe";

// Form-driven cloud-init generation (doc 33 §3). Ports vm-deploy's
// lib/cloudinit.sh generate_userdata / generate_metadata / generate_network_config
// from bash heredocs to deterministic TS. Fixed-format fields are validated and
// single-quoted; the variable runcmd tail (agent install) is safely encoded.

const DEFAULT_TIMEZONE = "Asia/Shanghai";
const SUDO_POLICY = "ALL=(ALL) NOPASSWD:ALL";
const MAX_DATA_DISKS = 9; // sdb..sdj

const DEFAULT_PACKAGES = [
  "open-vm-tools",
  "unattended-upgrades",
  "curl",
  "wget",
  "vim",
  "net-tools",
] as const;

export type NetworkConfig =
  | { mode: "dhcp" }
  | {
      mode: "static";
      ip: string;
      /** IPv4 CIDR prefix length, 0-32. */
      prefix: number;
      gateway: string;
      /** One or more nameserver addresses (first is required). */
      dns: string[];
    };

/** netplan v2 ethernets block matching `en*`, DHCP or static. */
export function generateNetworkConfig(net: NetworkConfig): string {
  if (net.mode === "dhcp") {
    return [
      "version: 2",
      "ethernets:",
      "  mainif:",
      "    match:",
      '      name: "en*"',
      "    dhcp4: true",
      "    dhcp6: false",
    ].join("\n");
  }

  if (!Number.isInteger(net.prefix) || net.prefix < 0 || net.prefix > 32) {
    throw new Error(`Invalid network prefix: ${net.prefix} (expected integer 0-32)`);
  }
  if (net.dns.length === 0) {
    throw new Error("Static network requires at least one DNS server");
  }

  const ip = yamlField("ip", net.ip).slice(1, -1); // validated; re-quoted below as CIDR
  const lines = [
    "version: 2",
    "ethernets:",
    "  mainif:",
    "    match:",
    '      name: "en*"',
    "    dhcp4: false",
    "    addresses:",
    `      - '${ip}/${net.prefix}'`,
    "    routes:",
    "      - to: default",
    `        via: ${yamlField("gateway", net.gateway)}`,
    "    nameservers:",
    "      addresses:",
    ...net.dns.map((d) => `        - ${yamlField("dns", d)}`),
  ];
  return lines.join("\n");
}

export interface MetadataParams {
  /** cloud-init instance-id; stable per VM (use the platform Instance id). */
  instanceId: string;
  hostname: string;
  network: NetworkConfig;
}

/** cloud-init metadata (instance-id + hostname + embedded network config). */
export function generateMetadata(p: MetadataParams): string {
  const network = generateNetworkConfig(p.network)
    .split("\n")
    .map((l) => `  ${l}`)
    .join("\n");
  return [
    `instance-id: ${yamlField("instanceId", p.instanceId)}`,
    `local-hostname: ${yamlField("hostname", p.hostname)}`,
    "network:",
    network,
    "",
  ].join("\n");
}

export interface UserdataParams {
  hostname: string;
  user: string;
  /** Plaintext password; SHA-512 hashed before it lands in cloud-init. */
  password: string;
  timezone?: string;
  /** SSH public key; omitted entirely from output when absent. */
  sshKey?: string;
  /** Extra apt packages appended after the defaults. */
  packages?: string[];
  /** Number of data disks (sdb..sdj) to format+mount; 0-9. */
  dataDisks?: number;
  /** Variable runcmd tail (agent install) appended after base steps. */
  runcmd?: string[];
  /** Fixed salt for deterministic output (tests); random otherwise. */
  passwordSalt?: string;
}

/** Full `#cloud-config` userdata document. */
export function generateUserdata(p: UserdataParams): string {
  const timezone = p.timezone ?? DEFAULT_TIMEZONE;
  const hashed = sha512Crypt(p.password, { salt: p.passwordSalt });

  const userLines = [
    `  - name: ${yamlField("user", p.user)}`,
    `    gecos: ${yamlField("user", p.user)}`,
    `    sudo: '${SUDO_POLICY}'`,
    "    shell: /bin/bash",
    "    groups: [sudo, adm, dialout, cdrom, audio, video, netdev]",
    "    lock_passwd: false",
    `    passwd: '${hashed}'`,
  ];
  if (p.sshKey) {
    userLines.push("    ssh_authorized_keys:", `      - ${yamlField("sshKey", p.sshKey)}`);
  }

  const extraPackages = (p.packages ?? []).map((pkg) => `  - ${yamlField("package", pkg)}`);
  const packageLines = [
    ...DEFAULT_PACKAGES.map((pkg) => `  - ${pkg}`),
    ...extraPackages,
  ];

  const runcmdLines = [
    "  - systemctl enable --now open-vm-tools",
    ...dataDiskRuncmd(p.dataDisks ?? 0),
    ...(p.runcmd ?? []).map((cmd) => `  - ${encodeRuncmd(cmd)}`),
  ];

  return [
    "#cloud-config",
    `hostname: ${yamlField("hostname", p.hostname)}`,
    `timezone: ${yamlField("timezone", timezone)}`,
    "users:",
    ...userLines,
    "package_update: true",
    "packages:",
    ...packageLines,
    "runcmd:",
    ...runcmdLines,
    "",
  ].join("\n");
}

// Format + mount each data disk: sdb→/data, sdc→/data1, ... (vm-deploy mapping).
function dataDiskRuncmd(count: number): string[] {
  if (!Number.isInteger(count) || count < 0 || count > MAX_DATA_DISKS) {
    throw new Error(`Invalid dataDisks: ${count} (expected integer 0-${MAX_DATA_DISKS})`);
  }
  const out: string[] = [];
  for (let idx = 0; idx < count; idx++) {
    const dev = `/dev/sd${String.fromCharCode("b".charCodeAt(0) + idx)}`;
    const mp = idx === 0 ? "/data" : `/data${idx}`;
    const script =
      `if [ -b ${dev} ] && ! blkid ${dev} >/dev/null 2>&1; then ` +
      `mkfs.ext4 -F ${dev} && mkdir -p ${mp} && ` +
      `echo "${dev} ${mp} ext4 defaults,nofail 0 2" >> /etc/fstab && ` +
      `mount ${mp} && chmod 755 ${mp}; fi`;
    out.push(`  - [ bash, -c, ${encodeRuncmd(script)} ]`);
  }
  return out;
}
