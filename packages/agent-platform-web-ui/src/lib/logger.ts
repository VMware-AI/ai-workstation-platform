// Minimal structured logger (harness H-12, #213): one JSON object per line,
// same shape as control's obs.JsonFormatter, so `grep <request-id>` works
// across web-ui, gateway, and control logs alike. Zero deps — console.* is
// what Next.js/PM2/systemd already capture.

type LogFields = Record<string, unknown>;

function serialize(level: string, message: string, fields?: LogFields): string {
  const payload: LogFields = {
    timestamp: new Date().toISOString(),
    level,
    message,
  };
  for (const [key, value] of Object.entries(fields ?? {})) {
    // Keep the stack (#224): for background-task failures it is often the
    // only clue. stack already includes the "Error: <message>" first line.
    payload[key] = value instanceof Error ? (value.stack ?? value.message) : value;
  }
  return JSON.stringify(payload);
}

export const logger = {
  info(message: string, fields?: LogFields): void {
    console.log(serialize("info", message, fields));
  },
  warn(message: string, fields?: LogFields): void {
    console.warn(serialize("warn", message, fields));
  },
  error(message: string, fields?: LogFields): void {
    console.error(serialize("error", message, fields));
  },
};
