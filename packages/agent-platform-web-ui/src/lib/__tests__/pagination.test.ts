import { describe, it, expect } from "vitest";
import { parsePageParams } from "../pagination";

function url(qs: string) {
  return new URL(`http://t/api/x${qs}`);
}

describe("parsePageParams (#255)", () => {
  it("无参数 = 旧行为（skip 0 / take 上限）", () => {
    expect(parsePageParams(url(""))).toEqual({ skip: 0, take: 200 });
  });

  it("合法翻页", () => {
    expect(parsePageParams(url("?skip=200&take=50"))).toEqual({ skip: 200, take: 50 });
  });

  it("maxTake 可配（billing 500）", () => {
    expect(parsePageParams(url("?take=500"), { maxTake: 500 })).toEqual({ skip: 0, take: 500 });
  });

  it.each(["?skip=-1", "?skip=abc", "?skip=1.5", "?skip=1000001", "?take=0", "?take=201", "?take=abc"])(
    "非法参数 %s → error",
    (qs) => {
      expect(parsePageParams(url(qs))).toHaveProperty("error");
    }
  );
});
