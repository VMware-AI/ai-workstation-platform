// YAML-safety helpers for cloud-init generation.
//
// Two distinct strategies, matching vm-deploy's split between fixed-format
// fields and pass-through install commands (doc 33 §3-4):
//
//   * Fixed fields (hostname, user, timezone, ip, ...) are inserted into
//     single-quoted YAML scalars. We REJECT dangerous characters outright
//     (vm-deploy's `_yaml_safe`) — these fields have no business containing
//     quotes, backslashes, `$`, backticks, or newlines.
//
//   * runcmd install commands ARE arbitrary shell (pipes, quotes, URLs) and
//     must NOT be rejected. They are SAFELY ENCODED as YAML double-quoted
//     scalars via JSON.stringify (JSON is a strict subset of YAML), so no
//     amount of metacharacters can break out of the list item.

const FORBIDDEN = /['"\\$`\n\r]/;

export class YamlFieldError extends Error {
  constructor(field: string, value: string) {
    super(
      `Invalid value for "${field}": must not contain quotes, backslash, '$', ` +
        `backtick, or newlines. Got: ${JSON.stringify(value)}`,
    );
    this.name = "YamlFieldError";
  }
}

/**
 * Validate a fixed-format field and return it single-quoted for YAML.
 * Throws YamlFieldError on dangerous characters — fail fast at the boundary.
 */
export function yamlField(field: string, value: string): string {
  if (FORBIDDEN.test(value)) throw new YamlFieldError(field, value);
  return `'${value}'`;
}

/** True if the value is safe to inline into a single-quoted YAML scalar. */
export function isYamlSafe(value: string): boolean {
  return !FORBIDDEN.test(value);
}

/**
 * Encode an arbitrary shell command as a YAML double-quoted scalar safe to use
 * as a `runcmd` list item. Newlines inside a single command are escaped to
 * `\n`; callers should pass one command per entry.
 */
export function encodeRuncmd(command: string): string {
  return JSON.stringify(command);
}
