import { describe, expect, it } from "vitest";
import { filterVmFolders } from "../govc";

describe("filterVmFolders", () => {
  // Real data from a live vCenter (datacenter "home"): `govc find . -type f`
  // returns every folder type, but only the vm subtree is a valid clone target.
  it("keeps only VM folders, drops host/network/datastore/root", () => {
    const all = [
      "/",
      "/home/datastore",
      "/home/host",
      "/home/network",
      "/home/vm",
      "/home/vm/Discovered virtual machine",
      "/home/vm/vCLS",
    ];
    expect(filterVmFolders(all)).toEqual([
      "/home/vm",
      "/home/vm/Discovered virtual machine",
      "/home/vm/vCLS",
    ]);
  });

  it("matches 'vm' only as a whole path segment, not as a substring", () => {
    // A folder literally named with 'vm' inside another word must NOT pass on
    // name alone; only a real `/vm/` segment qualifies.
    expect(filterVmFolders(["/dc/host/myvmware"])).toEqual([]);
    expect(filterVmFolders(["/dc/vm/myvmware"])).toEqual(["/dc/vm/myvmware"]);
  });

  it("returns empty when there are no VM folders", () => {
    expect(filterVmFolders(["/dc/host", "/dc/network"])).toEqual([]);
  });

  it("handles an empty list", () => {
    expect(filterVmFolders([])).toEqual([]);
  });
});
