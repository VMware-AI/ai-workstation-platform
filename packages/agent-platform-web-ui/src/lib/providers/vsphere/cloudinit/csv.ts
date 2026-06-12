import { isYamlSafe } from "./yaml-safe";
import type { NetworkConfig } from "./userdata";

// CSV batch parser for the deploy page (doc 33 §6.5). Each row is one VM with
// the columns: vm_name, ip, netmask, gateway, dns, user, password, ssh_key.
// Per-row identity + network only; shared inputs (timezone, packages, agent
// install runcmd, data disks, LLM) come from the page-level form and are merged
// at deploy time. An empty `ip` makes the row DHCP.
//
// Validation never throws: every bad row is collected with its line number so
// the UI can show all problems at once (vm-deploy batch-deploy.sh per-row).

export const CSV_COLUMNS = [
  "vm_name",
  "ip",
  "netmask",
  "gateway",
  "dns",
  "user",
  "password",
  "ssh_key",
] as const;

/** One validated VM row. hostname defaults to vm_name (no separate column). */
export interface CsvDeployRow {
  /** 1-based source line (header counts as line 1). */
  line: number;
  vmName: string;
  hostname: string;
  network: NetworkConfig;
  user: string;
  password: string;
  sshKey?: string;
}

export interface CsvRowError {
  line: number;
  message: string;
}

export interface CsvParseResult {
  rows: CsvDeployRow[];
  errors: CsvRowError[];
}

/**
 * Convert a network mask to a CIDR prefix length. Accepts a dotted IPv4 mask
 * (255.255.255.0), a bare prefix (24), or /-prefixed (/24). Returns null when
 * the mask is malformed or non-contiguous.
 */
export function netmaskToPrefix(value: string): number | null {
  const trimmed = value.trim().replace(/^\//, "");
  if (/^\d+$/.test(trimmed)) {
    const n = Number(trimmed);
    return n >= 0 && n <= 32 ? n : null;
  }
  const octets = trimmed.split(".");
  if (octets.length !== 4) return null;
  let bits = 0;
  for (const o of octets) {
    if (!/^\d+$/.test(o)) return null;
    const n = Number(o);
    if (n < 0 || n > 255) return null;
    bits = (bits << 8) | n;
  }
  // Must be a run of 1s followed by a run of 0s (contiguous mask).
  bits = bits >>> 0;
  const inverted = (~bits) >>> 0;
  if (((inverted + 1) & inverted) !== 0) return null;
  let prefix = 0;
  let b = bits;
  while (b & 0x80000000) {
    prefix++;
    b = (b << 1) >>> 0;
  }
  return prefix;
}

/** Parse CSV text into validated deploy rows + per-row errors. */
export function parseDeployCsv(text: string): CsvParseResult {
  const records = splitCsv(text);
  const rows: CsvDeployRow[] = [];
  const errors: CsvRowError[] = [];

  if (records.length === 0) return { rows, errors };

  // First non-empty record is the header; tolerate it being present or not by
  // detecting the known column names. Lines are 1-based including the header.
  const headerLooksValid = sameColumns(records[0].fields);
  if (!headerLooksValid) {
    errors.push({
      line: records[0].line,
      message: `Header must be: ${CSV_COLUMNS.join(", ")}`,
    });
    return { rows, errors };
  }

  for (const rec of records.slice(1)) {
    if (rec.fields.every((f) => f.trim() === "")) continue; // skip blank lines
    const parsed = parseRow(rec.fields, rec.line);
    if ("error" in parsed) errors.push({ line: rec.line, message: parsed.error });
    else rows.push(parsed.row);
  }

  return { rows, errors };
}

function sameColumns(fields: string[]): boolean {
  if (fields.length !== CSV_COLUMNS.length) return false;
  return fields.every((f, i) => f.trim().toLowerCase() === CSV_COLUMNS[i]);
}

type RowOutcome = { row: CsvDeployRow } | { error: string };

function parseRow(fields: string[], line: number): RowOutcome {
  if (fields.length !== CSV_COLUMNS.length) {
    return { error: `Expected ${CSV_COLUMNS.length} columns, got ${fields.length}` };
  }
  const [vmName, ip, netmask, gateway, dns, user, password, sshKey] = fields.map((f) => f.trim());

  if (!vmName) return { error: "vm_name is required" };
  if (!user) return { error: "user is required" };
  if (!password) return { error: "password is required" };

  for (const [name, val] of [
    ["vm_name", vmName],
    ["user", user],
    ["ssh_key", sshKey],
  ] as const) {
    if (val && !isYamlSafe(val)) return { error: `${name} contains unsafe characters` };
  }

  let network: NetworkConfig;
  if (!ip) {
    network = { mode: "dhcp" };
  } else {
    const prefix = netmaskToPrefix(netmask);
    if (prefix === null) return { error: `invalid netmask "${netmask}"` };
    if (!gateway) return { error: "gateway is required for a static IP" };
    const dnsServers = dns.split(/[;\s]+/).filter(Boolean);
    if (dnsServers.length === 0) return { error: "dns is required for a static IP" };
    for (const d of [ip, gateway, ...dnsServers]) {
      if (!isYamlSafe(d)) return { error: `network field "${d}" contains unsafe characters` };
    }
    network = { mode: "static", ip, prefix, gateway, dns: dnsServers };
  }

  return {
    row: {
      line,
      vmName,
      hostname: vmName,
      network,
      user,
      password,
      ...(sshKey ? { sshKey } : {}),
    },
  };
}

interface CsvRecord {
  line: number;
  fields: string[];
}

// Line-based reader: one record per line (host CSVs never embed newlines in a
// field), so line numbers are exact. Each line is split quote-aware so commas
// inside double-quoted fields (e.g. a password) are preserved; "" escapes a
// literal quote. CRLF and LF both handled.
function splitCsv(text: string): CsvRecord[] {
  const lines = text.split(/\r?\n/);
  const records: CsvRecord[] = [];
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    if (raw === "") continue; // skip empty physical lines (incl. trailing newline)
    records.push({ line: i + 1, fields: splitLine(raw) });
  }
  return records;
}

function splitLine(line: string): string[] {
  const fields: string[] = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inQuotes) {
      if (c === '"') {
        if (line[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += c;
      }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ",") {
      fields.push(field);
      field = "";
    } else {
      field += c;
    }
  }
  fields.push(field);
  return fields;
}
