import crypto from "node:crypto";

// SHA-512 crypt ("$6$") for the cloud-init `passwd:` field.
//
// vm-deploy's lib/cloudinit.sh hashes with `openssl passwd -6` (falling back
// to python3 crypt). We do not want to shell out from the web-ui worker, so
// this is a pure-Node port of Ulrich Drepper's SHA-512 crypt algorithm
// (https://www.akkadia.org/drepper/SHA-crypt.txt). Only the round-mixing and
// the custom base64 permutation are hand-written; the SHA-512 primitive is
// Node's crypto, so there is no bespoke hash code to get subtly wrong.
//
// Correctness is pinned by Drepper's official test vectors in the test file.

const DEFAULT_ROUNDS = 5000;
const MAX_SALT_LEN = 16;

// crypt(3) base64 alphabet — note this is NOT standard base64.
const ALPHABET =
  "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
const SALT_CHARS = ALPHABET; // valid characters for a generated salt

function sha512(...parts: Buffer[]): Buffer {
  const h = crypto.createHash("sha512");
  for (const p of parts) h.update(p);
  return h.digest();
}

// Emit `n` base64 chars from a 24-bit little-endian group (b2<<16 | b1<<8 | b0).
function b64From24Bit(b2: number, b1: number, b0: number, n: number): string {
  let w = (b2 << 16) | (b1 << 8) | b0;
  let out = "";
  for (let i = 0; i < n; i++) {
    out += ALPHABET[w & 0x3f];
    w >>>= 6;
  }
  return out;
}

// Permutation order for the 64-byte SHA-512 digest, per the reference.
const PERMUTATION: readonly (readonly [number, number, number])[] = [
  [0, 21, 42], [22, 43, 1], [44, 2, 23], [3, 24, 45], [25, 46, 4],
  [47, 5, 26], [6, 27, 48], [28, 49, 7], [50, 8, 29], [9, 30, 51],
  [31, 52, 10], [53, 11, 32], [12, 33, 54], [34, 55, 13], [56, 14, 35],
  [15, 36, 57], [37, 58, 16], [59, 17, 38], [18, 39, 60], [40, 61, 19],
  [62, 20, 41],
];

function encode(digest: Buffer): string {
  let out = "";
  for (const [a, b, c] of PERMUTATION) {
    out += b64From24Bit(digest[a], digest[b], digest[c], 4);
  }
  out += b64From24Bit(0, 0, digest[63], 2);
  return out;
}

// Build a byte sequence of `length` bytes by repeating `block`.
function repeatBytes(block: Buffer, length: number): Buffer {
  const out = Buffer.alloc(length);
  for (let off = 0; off < length; off += block.length) {
    block.copy(out, off, 0, Math.min(block.length, length - off));
  }
  return out;
}

export interface Sha512CryptOptions {
  /** Up to 16 chars from the crypt alphabet. Random if omitted. */
  salt?: string;
  /** Mixing rounds. Default 5000 (matches `openssl passwd -6`). */
  rounds?: number;
}

/**
 * Hash `password` as a SHA-512 crypt string suitable for /etc/shadow and the
 * cloud-init `passwd:` field. Returns `$6$<salt>$<86-char-hash>`.
 *
 * Pass a fixed `salt` for deterministic output (tests); otherwise a random
 * 16-char salt is generated. `rounds` defaults to 5000 and is only embedded as
 * `rounds=N$` when it differs from the default.
 */
export function sha512Crypt(password: string, options: Sha512CryptOptions = {}): string {
  const rounds = options.rounds ?? DEFAULT_ROUNDS;
  if (!Number.isInteger(rounds) || rounds < 1000 || rounds > 999_999_999) {
    throw new Error(`sha512Crypt: rounds must be an integer in [1000, 999999999], got ${rounds}`);
  }

  const key = Buffer.from(password, "utf8");
  const saltStr = (options.salt ?? randomSalt()).slice(0, MAX_SALT_LEN);
  const salt = Buffer.from(saltStr, "utf8");

  // Digest B = SHA512(key + salt + key).
  const altB = sha512(key, salt, key);

  // Digest A.
  const aParts: Buffer[] = [key, salt];
  for (let cnt = key.length; cnt > 0; cnt -= 64) {
    aParts.push(cnt > 64 ? altB : altB.subarray(0, cnt));
  }
  for (let cnt = key.length; cnt > 0; cnt >>>= 1) {
    aParts.push(cnt & 1 ? altB : key);
  }
  let digestA = sha512(...aParts);

  // P sequence: SHA512(key * key.length), repeated to key.length bytes.
  const dp = sha512(...Array<Buffer>(key.length).fill(key));
  const P = repeatBytes(dp, key.length);

  // S sequence: SHA512(salt * (16 + A[0])), repeated to salt.length bytes.
  const dsParts = Array<Buffer>(16 + digestA[0]).fill(salt);
  const ds = sha512(...dsParts);
  const S = repeatBytes(ds, salt.length);

  // Burn `rounds` cycles.
  for (let i = 0; i < rounds; i++) {
    const parts: Buffer[] = [];
    parts.push(i & 1 ? P : digestA);
    if (i % 3 !== 0) parts.push(S);
    if (i % 7 !== 0) parts.push(P);
    parts.push(i & 1 ? digestA : P);
    digestA = sha512(...parts);
  }

  const prefix = rounds === DEFAULT_ROUNDS ? "$6$" : `$6$rounds=${rounds}$`;
  return `${prefix}${saltStr}$${encode(digestA)}`;
}

function randomSalt(): string {
  const bytes = crypto.randomBytes(MAX_SALT_LEN);
  let out = "";
  for (let i = 0; i < MAX_SALT_LEN; i++) {
    out += SALT_CHARS[bytes[i] % SALT_CHARS.length];
  }
  return out;
}
