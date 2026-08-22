/**
 * Revocation helpers.
 *
 * The verb lives on `remit.authorization.revoke()` because revoking IS an
 * authorization operation. This module holds the reading side and the one
 * thing worth stating on its own:
 *
 * REVOCATION IS FORWARD ONLY. There is no un-revoke in the protocol, so there
 * is none in the SDK. A kill switch you can undo is a kill switch you have to
 * reason about under pressure.
 */

import type { Authority, Revocation } from "./types.js";

/** True when this authority can no longer authorise anything. */
export function isSpent(authority: Authority, now: Date = new Date()): boolean {
  if (authority.revoked) return true;
  const expires = Date.parse(authority.expires_at);
  return Number.isFinite(expires) && expires <= now.getTime();
}

/** Why an authority is unusable, in one sentence, or null if it is fine. */
export function whyUnusable(authority: Authority, now: Date = new Date()): string | null {
  if (authority.revoked) {
    return authority.revoked_at
      ? `revoked at ${authority.revoked_at}`
      : "revoked";
  }
  const expires = Date.parse(authority.expires_at);
  if (Number.isFinite(expires) && expires <= now.getTime()) {
    return `expired at ${authority.expires_at}`;
  }
  return null;
}

export type { Revocation };
