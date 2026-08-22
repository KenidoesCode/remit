/**
 * Terminal output.
 *
 * Colour only when the stream is a TTY and NO_COLOR is unset — a CLI that
 * writes escape codes into a log file is a CLI that makes logs unreadable.
 * No box-drawing characters either: they render as mojibake in several Windows
 * terminals, and a diagnostic tool that looks broken IS broken.
 */

const ESC = String.fromCharCode(27);
const env = process.env;

const useColor = process.stdout.isTTY === true && !env["NO_COLOR"] && env["TERM"] !== "dumb";

const wrap = (code: string) => (s: string) =>
  useColor ? `${ESC}[${code}m${s}${ESC}[0m` : s;

export const red = wrap("31");
export const green = wrap("32");
export const yellow = wrap("33");
export const dim = wrap("2");
export const bold = wrap("1");

export function line(status: "ok" | "fail" | "warn", label: string, detail?: string): string {
  const mark =
    status === "ok" ? green("  ok  ") : status === "warn" ? yellow(" warn ") : red(" FAIL ");
  return `${mark} ${label}${detail ? "  " + dim(detail) : ""}`;
}

export function heading(text: string): string {
  return "\n" + bold(text) + "\n" + dim("-".repeat(text.length)) + "\n";
}

/**
 * Redact anything shaped like a session before it reaches a terminal.
 *
 * Applied to every value the CLI prints, including error bodies, rather than
 * at the handful of call sites that "obviously" carry one. A redaction you
 * have to remember to call is a redaction that leaks the first time somebody
 * adds a log line.
 */
export function safe(value: unknown): string {
  const s = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return s
    .replace(/usr_[A-Za-z0-9_-]{6,}\.[a-f0-9]{8,}/g, "usr_REDACTED.REDACTED")
    .replace(/(REMIT_SESSION\s*[=:]\s*)\S+/gi, "$1REDACTED")
    .replace(/(bearer\s+)\S+/gi, "$1REDACTED");
}
