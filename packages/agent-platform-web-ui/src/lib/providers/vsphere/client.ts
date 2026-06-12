import { Agent } from "undici";

export interface VSphereConfig {
  host: string;
  username: string;
  password: string;
  datacenter: string;
  templateName: string;
  datastore?: string;
  resourcePool?: string;
  folder?: string;
  // Verify TLS certificate of the vCenter API (default: true).
  // Set to false ONLY for self-signed lab/dev clusters; doing so in production
  // exposes credentials to MitM. Prefer adding the vCenter CA to the trust
  // store via NODE_EXTRA_CA_CERTS instead.
  verifySsl?: boolean;
}

export interface CloneResult {
  id: string;
  name: string;
  ip?: string;
}

export class VSphereClient {
  private baseUrl: string;
  private authHeader: string;
  private config: VSphereConfig;
  private dispatcher: Agent;

  constructor(config: VSphereConfig) {
    this.config = config;
    this.baseUrl = `https://${config.host}/api`;
    this.authHeader = "Basic " + Buffer.from(`${config.username}:${config.password}`).toString("base64");
    this.dispatcher = new Agent({
      connect: { rejectUnauthorized: config.verifySsl !== false },
    });
  }

  private async fetch<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      // Node's global fetch accepts undici's `dispatcher` at runtime, but the
      // standard RequestInit TypeScript type omits it.
      dispatcher: this.dispatcher,
      headers: {
        Authorization: this.authHeader,
        "Content-Type": "application/json",
        ...(init?.headers as Record<string, string>),
      },
    } as RequestInit & { dispatcher: Agent });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(`vSphere API ${path} returned ${res.status}: ${body}`);
    }
    if (res.status === 204) return undefined as T;
    return res.json() as T;
  }

  /**
   * Probe connectivity + credentials with no side effects. POST /api/session
   * performs a vCenter login (returns a session token, which we discard). On
   * failure `fetch()` throws with the HTTP status + body so callers can turn it
   * into a teaching message. Read-only — safe for a "Test connection" button.
   */
  async testConnection(): Promise<void> {
    await this.fetch<string>("/session", { method: "POST" });
  }

  async findTemplate(): Promise<string> {
    const vms = await this.fetch<{ value: { vm: string; name: string }[] }>(
      `/vcenter/vm?filter.names=${encodeURIComponent(this.config.templateName)}`
    );
    const match = vms.value.find((v) => v.name === this.config.templateName);
    if (!match) throw new Error(`Template ${this.config.templateName} not found`);
    return match.vm;
  }

  async cloneFromTemplate(name: string, userData: string): Promise<CloneResult> {
    const templateId = await this.findTemplate();

    const spec: Record<string, unknown> = {
      name,
      placement: {
        ...(this.config.datastore ? { datastore: this.config.datastore } : {}),
        ...(this.config.folder ? { folder: this.config.folder } : {}),
        ...(this.config.resourcePool ? { resource_pool: this.config.resourcePool } : {}),
      },
      guest_customization_spec: {
        cloud_config: userData,
      },
      power_on: true,
    };

    const vmId = await this.fetch<string>(`/vcenter/vm?action=clone&source=${templateId}`, {
      method: "POST",
      body: JSON.stringify(spec),
    });

    let ip: string | undefined;
    for (let i = 0; i < 30; i++) {
      await new Promise((r) => setTimeout(r, 10000));
      ip = await this.getIpAddress(vmId);
      if (ip) break;
    }

    return { id: vmId, name, ip };
  }

  async getIpAddress(vmId: string): Promise<string | undefined> {
    try {
      const info = await this.fetch<{ guest_OS?: { ip_address?: string } }>(
        `/vcenter/vm/${vmId}/guest/identity`
      );
      return info.guest_OS?.ip_address || undefined;
    } catch {
      return undefined;
    }
  }

  async powerOn(vmId: string): Promise<void> {
    await this.fetch<void>(`/vcenter/vm/${vmId}/power?action=start`, { method: "POST" });
  }

  async powerOff(vmId: string, graceful: boolean): Promise<void> {
    const action = graceful ? "shutdown" : "stop";
    await this.fetch<void>(`/vcenter/vm/${vmId}/power?action=${action}`, { method: "POST" });
  }

  async deleteVm(vmId: string): Promise<void> {
    await this.fetch<void>(`/vcenter/vm/${vmId}`, { method: "DELETE" });
  }
}
