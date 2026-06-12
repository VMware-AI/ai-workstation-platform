import { describe, it, expect, vi, afterEach } from "vitest";
import { logger } from "../logger";

afterEach(() => {
  vi.restoreAllMocks();
});

function lastJsonLine(spy: ReturnType<typeof vi.spyOn>): Record<string, unknown> {
  expect(spy).toHaveBeenCalledTimes(1);
  const line = spy.mock.calls[0][0] as string;
  return JSON.parse(line);
}

describe("logger", () => {
  it("emits one JSON line with level/message/timestamp", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    logger.error("save failed");
    const rec = lastJsonLine(spy);
    expect(rec.level).toBe("error");
    expect(rec.message).toBe("save failed");
    expect(typeof rec.timestamp).toBe("string");
  });

  it("includes extra fields like requestId", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    logger.error("boom", { requestId: "trace-1", scope: "compute-pools" });
    const rec = lastJsonLine(spy);
    expect(rec.requestId).toBe("trace-1");
    expect(rec.scope).toBe("compute-pools");
  });

  it("serializes Error values into message strings", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    logger.error("op failed", { error: new Error("bad thing") });
    const rec = lastJsonLine(spy);
    expect(rec.error).toContain("bad thing");
  });

  it("#224: Error values keep their stack trace (background-task forensics)", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    logger.error("op failed", { error: new Error("bad thing") });
    const rec = lastJsonLine(spy);
    // stack starts with "Error: bad thing" then "    at ..." frames
    expect(rec.error).toMatch(/^Error: bad thing\n\s+at /);
  });

  it("#224: a stackless Error still serializes to its message", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const e = new Error("no stack");
    e.stack = undefined;
    logger.error("op failed", { error: e });
    const rec = lastJsonLine(spy);
    expect(rec.error).toBe("no stack");
  });

  it("info goes to console.log as JSON", () => {
    const spy = vi.spyOn(console, "log").mockImplementation(() => {});
    logger.info("started");
    const rec = lastJsonLine(spy);
    expect(rec.level).toBe("info");
  });

  it("warn goes to console.warn as JSON", () => {
    const spy = vi.spyOn(console, "warn").mockImplementation(() => {});
    logger.warn("careful");
    const rec = lastJsonLine(spy);
    expect(rec.level).toBe("warn");
  });
});
