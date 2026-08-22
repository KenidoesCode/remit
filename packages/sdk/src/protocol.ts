/** Protocol constants and compatibility checking. */

import { RemitProtocolError } from "./errors.js";

/** The protocol major version this SDK speaks. */
export const SUPPORTED_PROTOCOL_MAJOR = 1;

/** The wire prefix. The SDK targets /v1 explicitly and never guesses. */
export const API_PREFIX = "/v1";

export const SDK_VERSION = "0.1.0";

/**
 * Compare the server's protocol version against what this SDK understands.
 *
 * A differing MINOR is fine — the protocol adds fields, and a client that
 * refuses to run against a newer server is a client that breaks on a Tuesday.
 * A differing MAJOR is not, because that is what a major means.
 */
export function assertCompatible(serverVersion: string): void {
  const major = Number.parseInt(String(serverVersion).split(".")[0] ?? "", 10);
  if (!Number.isFinite(major)) {
    throw new RemitProtocolError(`server reported an unreadable protocol version: ${serverVersion}`, {
      code: "protocol_unreadable",
    });
  }
  if (major !== SUPPORTED_PROTOCOL_MAJOR) {
    throw new RemitProtocolError(
      `server speaks protocol ${serverVersion}; this SDK (${SDK_VERSION}) speaks ` +
        `${SUPPORTED_PROTOCOL_MAJOR}.x. Upgrade remit-sdk.`,
      { code: "protocol_incompatible" },
    );
  }
}

/** True when a differing minor version means the server is ahead of us. */
export function serverIsAhead(serverVersion: string): boolean {
  const [maj, min] = String(serverVersion).split(".").map((n) => Number.parseInt(n, 10));
  if (maj !== SUPPORTED_PROTOCOL_MAJOR) return false;
  return Number.isFinite(min) && (min as number) > 0;
}
