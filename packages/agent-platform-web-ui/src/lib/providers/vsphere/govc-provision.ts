import {
  cloneOff,
  injectGuestinfo,
  powerOn,
  waitForIp,
  destroyVm,
  GovcError,
  type CloneSpec,
  type GovcRunner,
} from "./govc";

// Full govc provision sequence (doc 33 §2, P2-c): clone powered-off → inject
// gzip+base64 guestinfo → power on → wait for IP. On ANY failure, best-effort
// roll back by destroying the clone so a half-built VM isn't left behind
// (vm-deploy's per-VM rollback). Takes a GovcRunner so it is unit-testable.

export interface ProvisionViaGovcOptions {
  /** Seconds to wait for the guest IP after power-on. */
  waitIpSeconds?: number;
}

export interface GovcProvisionResult {
  vmName: string;
  ip: string;
}

export async function provisionViaGovc(
  run: GovcRunner,
  spec: CloneSpec,
  docs: { userdata: string; metadata: string },
  opts: ProvisionViaGovcOptions = {},
): Promise<GovcProvisionResult> {
  const waitIpSeconds = opts.waitIpSeconds ?? 300;
  try {
    await cloneOff(run, spec);
    await injectGuestinfo(run, spec.name, docs);
    await powerOn(run, spec.name);
    const ip = await waitForIp(run, spec.name, waitIpSeconds);
    return { vmName: spec.name, ip };
  } catch (e) {
    // Best-effort rollback; ignore "not found" if the clone never landed.
    try {
      await destroyVm(run, spec.name);
    } catch {
      /* rollback is best-effort */
    }
    throw e instanceof GovcError
      ? e
      : new GovcError(`制备失败：${e instanceof Error ? e.message : String(e)}`);
  }
}
