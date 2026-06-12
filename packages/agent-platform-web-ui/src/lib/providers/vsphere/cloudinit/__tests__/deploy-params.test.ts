import { describe, expect, it } from "vitest";
import {
  buildInstancesFromDeploy,
  MAX_BATCH,
  SECRET_DEPLOY_FIELDS,
  type DeployRequest,
  type SharedDeploy,
} from "../deploy-params";
import { CSV_COLUMNS } from "../csv";

const shared: SharedDeploy = {
  templateName: "ubuntu-24-tmpl",
  datastore: "ds1",
  network: "VM Network",
  timezone: "UTC",
  agentType: "goose",
};

describe("buildInstancesFromDeploy — single", () => {
  const single: DeployRequest = {
    mode: "single",
    shared,
    vm: { name: "web-01", network: { mode: "dhcp" }, osUser: "ops", osPassword: "pw" },
  };

  it("expands one instance with merged shared + vm params", () => {
    const { instances, errors } = buildInstancesFromDeploy(single);
    expect(errors).toEqual([]);
    expect(instances).toHaveLength(1);
    expect(instances[0].name).toBe("web-01");
    expect(instances[0].deployParams).toMatchObject({
      templateName: "ubuntu-24-tmpl",
      datastore: "ds1",
      hostname: "web-01",
      osUser: "ops",
      osPassword: "pw",
      agentType: "goose",
      netConfig: { mode: "dhcp" },
    });
  });

  it("carries a static network through", () => {
    const { instances } = buildInstancesFromDeploy({
      ...single,
      vm: {
        name: "web-02",
        osUser: "ops",
        osPassword: "pw",
        network: { mode: "static", ip: "10.0.1.5", prefix: 24, gateway: "10.0.1.1", dns: ["8.8.8.8"] },
      },
    });
    expect(instances[0].deployParams.netConfig).toEqual({
      mode: "static",
      ip: "10.0.1.5",
      prefix: 24,
      gateway: "10.0.1.1",
      dns: ["8.8.8.8"],
    });
  });

  it.each([
    [{ ...shared, templateName: "" }, /templateName/],
    [shared, /osUser|osPassword/],
  ])("rejects missing required fields", (sh, re) => {
    const req: DeployRequest = {
      mode: "single",
      shared: sh as SharedDeploy,
      vm: { name: "x", network: { mode: "dhcp" }, osUser: "", osPassword: "" },
    };
    const { instances, errors } = buildInstancesFromDeploy(req);
    expect(instances).toEqual([]);
    expect(errors.some((e) => re.test(e.message))).toBe(true);
  });

  it("rejects an unknown agent type and unsafe placement", () => {
    const r1 = buildInstancesFromDeploy({ ...single, shared: { ...shared, agentType: "nope" } });
    expect(r1.errors.some((e) => /agentType/.test(e.message))).toBe(true);
    const r2 = buildInstancesFromDeploy({ ...single, shared: { ...shared, datastore: "ds'1" } });
    expect(r2.errors.some((e) => /unsafe/.test(e.message))).toBe(true);
  });
});

describe("buildInstancesFromDeploy — batch", () => {
  const header = CSV_COLUMNS.join(",");

  it("expands a CSV into instances with shared placement merged", () => {
    const csv = [
      header,
      "web-01,10.0.1.5,255.255.255.0,10.0.1.1,8.8.8.8,ops,pw,",
      "web-02,,,,,ops,pw2,",
    ].join("\n");
    const { instances, errors } = buildInstancesFromDeploy({ mode: "batch", shared, csv });
    expect(errors).toEqual([]);
    expect(instances.map((i) => i.name)).toEqual(["web-01", "web-02"]);
    expect(instances[0].deployParams.templateName).toBe("ubuntu-24-tmpl");
    expect(instances[0].deployParams.osPassword).toBe("pw");
    expect(instances[1].deployParams.netConfig).toEqual({ mode: "dhcp" });
  });

  it("propagates per-row CSV errors and yields no instances", () => {
    const csv = [header, ",,,,,ops,pw,"].join("\n"); // missing vm_name
    const { instances, errors } = buildInstancesFromDeploy({ mode: "batch", shared, csv });
    expect(instances).toEqual([]);
    expect(errors[0].line).toBe(2);
  });

  it("rejects a batch larger than MAX_BATCH", () => {
    const rows = Array.from({ length: MAX_BATCH + 1 }, (_, i) => `vm-${i},,,,,ops,pw,`);
    const csv = [header, ...rows].join("\n");
    const { instances, errors } = buildInstancesFromDeploy({ mode: "batch", shared, csv });
    expect(instances).toEqual([]);
    expect(errors.some((e) => /Batch too large/.test(e.message))).toBe(true);
  });
});

describe("SECRET_DEPLOY_FIELDS", () => {
  it("names the fields the API must encrypt", () => {
    expect(SECRET_DEPLOY_FIELDS).toEqual(["osPassword", "llmApiKey"]);
  });
});
