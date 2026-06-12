import type { DeployParams } from "./vsphere/cloudinit/deploy-params";

export type InstanceStatus =
  | "PENDING"
  | "PROVISIONING"
  | "INITIALIZING"
  | "RUNNING"
  | "STOPPING"
  | "STOPPED"
  | "ERROR"
  | "DELETED";

export interface ProvisionInput {
  instanceId: string;
  tenantId: string;
  /** Clone target VM name (Instance.name). */
  vmName: string;
  /** Native vSphere deploy form (doc 33), with secrets already DECRYPTED. */
  deployParams: DeployParams;
  computePoolId?: string;
  /** One-shot agent bootstrap (#231): plaintext token (hash is already on
   *  the instance row) + the control-plane base URL the VM calls back to. */
  bootstrap?: { token: string; controlPlaneUrl: string };
}

export interface ProvisionResult {
  vmRefId?: string;
  ipAddress?: string;
  endpoint?: string;
  containerId?: string;
}

export interface RuntimeStats {
  cpuPercent: number;
  memoryMb: number;
}

export interface ComputeProvider {
  readonly name: string;
  provision(input: ProvisionInput): Promise<ProvisionResult>;
  start(instanceId: string, vmRefId?: string): Promise<void>;
  stop(instanceId: string, vmRefId?: string): Promise<void>;
  destroy(instanceId: string, vmRefId?: string): Promise<void>;
  getStats(instanceId: string, vmRefId?: string): Promise<RuntimeStats>;
  getLogs(instanceId: string, vmRefId?: string, tail?: number): Promise<string[]>;
}
