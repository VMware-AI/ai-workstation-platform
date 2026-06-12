import { ComputeProvider, ProvisionInput, ProvisionResult, RuntimeStats } from "../types";
import { prisma } from "@/lib/prisma";
import { decryptPoolPassword } from "@/lib/pool-config";
import { buildDeployCloudInit } from "./cloudinit";
import { cloudInitLogSummary, persistCloudInitDebug } from "./cloudinit/debug";
import { logger } from "@/lib/logger";
import {
  makeRunner,
  powerOn,
  destroyVm,
  type CloneSpec,
  type GovcRunner,
  type VcConn,
} from "./govc";
import { provisionViaGovc } from "./govc-provision";

// vSphere provider (doc 33): clone a VC template and inject form-driven
// cloud-init via govc + guestinfo. The legacy REST clone path and the
// docker-agent cloud-init were removed — provisioning is driven entirely by
// the Instance's deployParams (P2-c).
export class VSphereProvider implements ComputeProvider {
  readonly name = "vsphere";

  private connFromConfig(config: Record<string, unknown>): VcConn {
    return {
      host: String(config.host ?? ""),
      username: String(config.username ?? ""),
      password: decryptPoolPassword(config),
      datacenter: config.datacenter ? String(config.datacenter) : undefined,
      verifySsl: config.verifySsl === false ? false : undefined,
    };
  }

  async provision(input: ProvisionInput): Promise<ProvisionResult> {
    const pool = input.computePoolId
      ? await prisma.computePool.findUnique({ where: { id: input.computePoolId } })
      : null;
    if (!pool) throw new Error("vSphere compute pool not found");

    const conn = this.connFromConfig(pool.config as Record<string, unknown>);
    const dp = input.deployParams; // secrets already decrypted by the worker

    // LLM url/key are delivered to the agent at runtime (doc 33 §5.5), not baked
    // into cloud-init, so they are intentionally not passed here.
    const { userdata, metadata } = buildDeployCloudInit({
      instanceId: input.instanceId,
      hostname: dp.hostname,
      network: dp.netConfig,
      user: dp.osUser,
      password: dp.osPassword,
      timezone: dp.timezone,
      sshKey: dp.sshKey,
      packages: dp.packages,
      dataDisks: dp.dataDisks,
      agentType: dp.agentType,
      installCommands: dp.installCommands,
      bootstrap: input.bootstrap,
    });

    // Observability (#277): the docs are injected inline and never otherwise
    // persisted. Log a redacted summary always; dump the raw docs only when
    // CLOUDINIT_DEBUG_DIR is set (they contain secrets — see debug.ts).
    try {
      logger.info("cloud-init built for deploy", {
        vmName: input.vmName,
        ...cloudInitLogSummary({ userdata, metadata }, dp.agentType),
      });
      const dir = persistCloudInitDebug(input.vmName, { userdata, metadata });
      if (dir) {
        logger.warn("cloud-init RAW docs persisted for debug — contains secrets, delete after use", {
          dir,
          vmName: input.vmName,
        });
      }
    } catch (e) {
      logger.warn("cloud-init debug persist failed (provision continues)", {
        error: e instanceof Error ? e.message : String(e),
      });
    }

    const spec: CloneSpec = {
      templateName: dp.templateName,
      name: input.vmName,
      datastore: dp.datastore,
      network: dp.network,
      resourcePool: dp.resourcePool,
      folder: dp.folder,
    };

    const result = await provisionViaGovc(makeRunner(conn), spec, { userdata, metadata });
    return { vmRefId: result.vmName, ipAddress: result.ip };
  }

  async start(instanceId: string, vmRefId?: string): Promise<void> {
    const run = await this.runnerForInstance(instanceId);
    if (!run || !vmRefId) return;
    await powerOn(run, vmRefId);
  }

  async stop(instanceId: string, vmRefId?: string): Promise<void> {
    const run = await this.runnerForInstance(instanceId);
    if (!run || !vmRefId) return;
    await run(["vm.power", "-off", vmRefId]);
  }

  async destroy(instanceId: string, vmRefId?: string): Promise<void> {
    const run = await this.runnerForInstance(instanceId);
    if (!run || !vmRefId) return;
    await run(["vm.power", "-off", vmRefId]).catch(() => {});
    await destroyVm(run, vmRefId);
  }

  async getStats(_instanceId: string, _vmRefId?: string): Promise<RuntimeStats> {
    // Telemetry is reported by the in-VM agent (doc 33 §5.5); empty for now.
    return { cpuPercent: 0, memoryMb: 0 };
  }

  async getLogs(_instanceId: string, _vmRefId?: string, _tail = 100): Promise<string[]> {
    return [];
  }

  private async runnerForInstance(instanceId: string): Promise<GovcRunner | null> {
    const instance = await prisma.instance.findUnique({
      where: { id: instanceId },
      include: { computePool: true },
    });
    if (!instance?.computePool) return null;
    return makeRunner(this.connFromConfig(instance.computePool.config as Record<string, unknown>));
  }
}

export const vsphereProvider = new VSphereProvider();
