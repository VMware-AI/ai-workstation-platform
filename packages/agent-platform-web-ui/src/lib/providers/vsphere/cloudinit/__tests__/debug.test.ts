import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { cloudInitLogSummary, persistCloudInitDebug } from "../debug";

const DOCS = { userdata: "#cloud-config\nhostname: vm1\n", metadata: "instance-id: i-1\n" };

describe("cloudInitLogSummary", () => {
  it("reports byte sizes + agentType, and the registry install for a known agent", () => {
    const s = cloudInitLogSummary(DOCS, "xiaoguai");
    expect(s.userdataBytes).toBe(Buffer.byteLength(DOCS.userdata));
    expect(s.metadataBytes).toBe(Buffer.byteLength(DOCS.metadata));
    expect(s.agentType).toBe("xiaoguai");
    // pure registry preview still carries the placeholder (resolved later).
    expect(String(s.agentInstall)).toContain("xiaoguai");
  });

  it("omits agentInstall for none/unknown (no throw)", () => {
    expect(cloudInitLogSummary(DOCS, "").agentInstall).toBeUndefined();
    expect(cloudInitLogSummary(DOCS, undefined).agentType).toBe("none");
    expect(cloudInitLogSummary(DOCS, "k8s").agentInstall).toBeUndefined();
  });

  it("never leaks secrets present in userdata (the #1 safety bar)", () => {
    const secretDocs = {
      userdata:
        "#cloud-config\n" +
        "  - curl -H 'Authorization: Bearer faketoken-DEADBEEF' http://cp/register\n" +
        "chpasswd: { list: 'u:$6$salt$f4keHashDoNotLog' }\n",
      metadata: "instance-id: i-1\n",
    };
    const dumped = JSON.stringify(cloudInitLogSummary(secretDocs, "xiaoguai"));
    expect(dumped).not.toContain("faketoken-DEADBEEF");
    expect(dumped).not.toContain("$6$salt$");
    expect(dumped).not.toContain("Bearer");
  });
});

describe("persistCloudInitDebug", () => {
  let dir: string | undefined;
  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
    dir = undefined;
  });

  it("is a no-op when no dir is configured", () => {
    // Pass "" explicitly so the result doesn't depend on the runner's
    // CLOUDINIT_DEBUG_DIR (undefined would fall through to the env default).
    expect(persistCloudInitDebug("vm1", DOCS, "")).toBeNull();
  });

  it("writes the raw userdata + metadata, sanitizing the vm name", () => {
    dir = mkdtempSync(join(tmpdir(), "ci-debug-"));
    const out = persistCloudInitDebug("vm/../1", DOCS, dir);
    expect(out).toBe(dir);
    expect(readFileSync(join(dir, "vm_.._1.userdata.yaml"), "utf8")).toBe(DOCS.userdata);
    expect(readFileSync(join(dir, "vm_.._1.metadata.yaml"), "utf8")).toBe(DOCS.metadata);
  });
});
