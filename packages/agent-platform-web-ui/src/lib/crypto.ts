import crypto from "crypto";

// AES-256-GCM provides authenticated encryption (CBC alone is malleable).
// Key MUST be supplied via env var — no fallback. 32 bytes, hex-encoded.
// Generate one with: openssl rand -hex 32
const ALGORITHM = "aes-256-gcm";

function loadKey(): Buffer {
  const keyHex = process.env.ENCRYPTION_KEY;
  if (!keyHex) {
    throw new Error(
      "ENCRYPTION_KEY env var is required (64 hex chars = 32 bytes). " +
        "Generate with: openssl rand -hex 32"
    );
  }
  const key = Buffer.from(keyHex, "hex");
  if (key.length !== 32) {
    throw new Error(
      `ENCRYPTION_KEY must decode to exactly 32 bytes; got ${key.length}. ` +
        "Generate with: openssl rand -hex 32"
    );
  }
  return key;
}

let cachedKey: Buffer | undefined;
function getKey(): Buffer {
  if (!cachedKey) cachedKey = loadKey();
  return cachedKey;
}

// Format: <iv-hex>:<tag-hex>:<ciphertext-hex>
// GCM uses a 96-bit (12-byte) IV by NIST recommendation.

export function encrypt(text: string): string {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv(ALGORITHM, getKey(), iv);
  const encrypted = Buffer.concat([cipher.update(text, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return iv.toString("hex") + ":" + tag.toString("hex") + ":" + encrypted.toString("hex");
}

export function decrypt(encryptedText: string): string {
  const parts = encryptedText.split(":");
  if (parts.length !== 3) {
    throw new Error("Invalid ciphertext format (expected iv:tag:ciphertext)");
  }
  const [ivHex, tagHex, encHex] = parts;
  const iv = Buffer.from(ivHex, "hex");
  const tag = Buffer.from(tagHex, "hex");
  const encrypted = Buffer.from(encHex, "hex");
  const decipher = crypto.createDecipheriv(ALGORITHM, getKey(), iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(encrypted), decipher.final()]).toString("utf8");
}

export function maskApiKey(key: string): string {
  if (key.length <= 8) return "****";
  return key.slice(0, 4) + "****" + key.slice(-4);
}
