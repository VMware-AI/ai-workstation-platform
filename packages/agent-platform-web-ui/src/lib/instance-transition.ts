import type { InstanceStatus, Prisma } from "@prisma/client";
import { prisma } from "./prisma";

// Guarded instance status transition (#236, shared per #244): the row moves
// to `to` only if it is currently in one of `from` — Prisma updateMany makes
// the check-and-set a single atomic statement. Returns false when another
// writer owns the row: request handlers turn that into a 409, background
// completions (route IIFEs, provision worker, reaper) drop their write so
// the overtaking writer's outcome wins. DELETED is never in a from-set, so
// no writer can resurrect a deleted row.
export async function transitionInstance(
  id: string,
  from: InstanceStatus[],
  to: InstanceStatus,
  extra: Omit<Prisma.InstanceUpdateManyMutationInput, "status"> = {},
  // Extra ownership predicate (PR #253 review M-2): e.g. the provision
  // worker fences its post-claim writes with provisionerJobId so a stale
  // requeued job can never clobber the row a newer job owns.
  extraWhere: Omit<Prisma.InstanceWhereInput, "id" | "status"> = {}
): Promise<boolean> {
  const res = await prisma.instance.updateMany({
    where: { id, status: { in: from }, ...extraWhere },
    data: { status: to, ...extra },
  });
  return res.count > 0;
}
