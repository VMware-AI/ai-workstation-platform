import { describe, expect, it } from "vitest";
import { sha512Crypt } from "../sha512-crypt";

// Correctness is pinned against Ulrich Drepper's official SHA-512 crypt test
// vectors (https://www.akkadia.org/drepper/SHA-crypt.txt). If the round-mixing
// or the custom base64 permutation ever drifts, these fail loudly — long before
// a wrong hash silently locks an operator out of a deployed VM.

describe("sha512Crypt — Drepper vectors", () => {
  it("hashes with the default 5000 rounds (no rounds= prefix)", () => {
    expect(sha512Crypt("Hello world!", { salt: "saltstring" })).toBe(
      "$6$saltstring$svn8UoSVapNtMuq1ukKS4tPQd8iKwSMHWjl/O817G3uBnIFNjnQJuesI68u4OTLiBFdcbYEdFCoEOfaS35inz1",
    );
  });

  it("embeds rounds= and truncates the salt to 16 chars", () => {
    expect(
      sha512Crypt("Hello world!", { salt: "saltstringsaltstring", rounds: 10000 }),
    ).toBe(
      "$6$rounds=10000$saltstringsaltst$OW1/O6BYHV6BcXZu8QVeXbDWra3Oeqh0sbHbbMCVNSnCM/UrjmM0Dp8vOuZeHBy/YTBmSK6H9qs/y3RnOaw5v.",
    );
  });

  it("matches the long-salt vector's hash body (rounds=5000 default)", () => {
    // Drepper vector 3 specifies rounds=5000 explicitly; our default omits the
    // prefix, so compare only the salt+hash body, which is rounds-independent.
    const out = sha512Crypt("This is just a test", { salt: "toolongsaltstring" });
    expect(out).toBe(
      "$6$toolongsaltstrin$lQ8jolhgVRVhY4b5pZKaysCLi0QBxGoNeKQzQ3glMhwllF7oGDZxUhx1yxdYcz/e1JSbq3y6JMxxl8audkUEm0",
    );
  });
});

describe("sha512Crypt — behaviour", () => {
  it("produces a well-formed $6$ hash with a random salt", () => {
    const out = sha512Crypt("hunter2");
    expect(out).toMatch(/^\$6\$[./0-9A-Za-z]{1,16}\$[./0-9A-Za-z]{86}$/);
  });

  it("is deterministic for a fixed salt and random across calls", () => {
    expect(sha512Crypt("pw", { salt: "abc" })).toBe(sha512Crypt("pw", { salt: "abc" }));
    expect(sha512Crypt("pw")).not.toBe(sha512Crypt("pw"));
  });

  it("differs when the salt differs", () => {
    expect(sha512Crypt("pw", { salt: "aaa" })).not.toBe(sha512Crypt("pw", { salt: "bbb" }));
  });

  it("rejects out-of-range rounds", () => {
    expect(() => sha512Crypt("pw", { rounds: 999 })).toThrow(/rounds/);
    expect(() => sha512Crypt("pw", { rounds: 1_000_000_000 })).toThrow(/rounds/);
  });
});
