import { ComputeProvider } from "./types";
import { vsphereProvider } from "./vsphere";

/**
 * Resolve the compute provider backing an instance's compute pool.
 *
 * The docker-agent runtime was removed (doc 33 §7): agents run inside the
 * delivered vCenter VM, not in a platform-hosted container. vSphere is the
 * only supported backend. Returns null for pools we can't service so callers
 * decide how strict to be — the worker throws, lifecycle/telemetry routes
 * degrade gracefully (no live infra call, DB state still updates).
 */
export function resolveProvider(poolType?: string | null): ComputeProvider | null {
  if (poolType === "vsphere") return vsphereProvider;
  return null;
}
