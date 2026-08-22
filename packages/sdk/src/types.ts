/**
 * The REMIT protocol, as TypeScript.
 *
 * Every type here is a projection of `remit/protocol.py`. They are not a
 * parallel model and must not become one: a client that reimplements the
 * server's types is a client that will disagree with it, and the first thing
 * anyone finds is the disagreement.
 *
 * Amounts are ALWAYS paise and always carry a currency. An amount without a
 * unit is not an amount — the gap that let "under $5,000" become a ₹5,000
 * ceiling lived exactly there.
 */

/** The three things REMIT can say. There is no fourth. */
export type Verdict = "AUTO" | "STEP_UP" | "DENY";

/** Money, never a bare number. Paise, because integers do not round. */
export interface Money {
  amount_paise: number;
  currency: string;
}

/** One named rule and what it saw. `clause_id` is stable across versions;
 *  `detail` is human-readable and is not. */
export interface Clause {
  clause_id: string;
  passed: boolean;
  detail: string;
}

/**
 * What the human said, and what it was understood to mean.
 *
 * `utterance` is the evidence; everything below it is interpretation. They are
 * kept separate on purpose, so an agent that disagrees with the interpretation
 * can say so against the original words.
 */
export interface Intent {
  intent_id: string;
  actor_id: string;
  utterance: string;
  semantic_hash: string;
  category: string | null;
  /** the nouns the human actually said */
  requested: string[];
  /** and what they ruled out */
  excluded: string[];
  quantity: number;
  ceiling: Money | null;
  objective: string;
  merchants: string[];
  created_at: string;
  expires_at: string;
  policy_version: string;
  catalog_version: number;
  /** which intelligence produced this reading */
  interpreter: string;
  confidence: number;
}

/**
 * The bounded envelope and its lifecycle state.
 *
 * `state` is the AUTHORITY machine's, not the payment's. They answer different
 * questions: what the human permitted, versus what the gateway did.
 */
export interface Authority {
  intent_id: string;
  actor_id: string;
  state: string;
  ceiling: Money | null;
  expires_at: string;
  revoked: boolean;
  revoked_at: string | null;
  version: number;
}

/** A verdict with every clause behind it. */
export interface Decision {
  verdict: Verdict;
  reason: string;
  clauses: Clause[];
  /** clause ids that did not pass */
  failed: string[];
  drift: number | null;
  total: Money | null;
  authority_state: string | null;
  correlation_id: string;
  latency_ms: number | null;
  protocol_version: string;
}

/**
 * The money.
 *
 * `replayed` is part of the contract, not an implementation detail: a client
 * retrying a request needs to know the payment it is looking at is the one it
 * already made rather than a second one.
 */
export interface Execution {
  correlation_id: string;
  payment_id: string | null;
  order_id: string | null;
  state: string;
  total: Money | null;
  replayed: boolean;
  checkout_key_id: string | null;
}

/** Enough to answer "why did this happen" without asking the model. */
export interface Evidence {
  correlation_id: string;
  intent_id: string | null;
  events: AuditEvent[];
  decision: Record<string, unknown> | null;
  authority_history: Record<string, unknown>[];
  /** whether the hash chain verified end to end */
  chain_intact: boolean;
  first_bad_seq: number | null;
  protocol_version?: string;
}

export interface AuditEvent {
  seq: number;
  ts: string;
  kind: string;
  payload: Record<string, unknown>;
  hash: string;
}

export interface Revocation {
  scope: "intent" | "principal";
  target?: string;
  revoked_at: string;
  revoked_by?: string;
  reason?: string | null;
  protocol_version?: string;
}

/** What `intents.create()` returns: the reading, and the authority it opened. */
export interface IntentCreated {
  intent: Intent;
  authority: Authority;
  protocol_version: string;
}

/** `authorization.evaluate()` — a decision, and nothing moved. */
export interface Evaluation extends Decision {
  /** true only when the verdict is AUTO */
  would_execute: boolean;
  /** always true: evaluate runs on a throwaway instance */
  sandboxed: boolean;
  intent: Intent;
}

/** `payments.execute()` — the decision AND what the money did. */
export interface ExecutionResult {
  decision: Decision;
  execution: Execution;
  approval: ApprovalRequest | null;
  authority_state: string | null;
  protocol_version: string;
  /** present only when the authority was revoked before the engine was asked */
  revocation?: Revocation;
}

/** The token a human's "yes" is bound to. Single use, bound to one basket. */
export interface ApprovalRequest {
  token?: string;
  expires_at?: string;
  [key: string]: unknown;
}

/** What is being asked of the human, in a shape a client can render without
 *  knowing anything about baskets. */
export interface StepUp {
  required: boolean;
  asking?: {
    why: string;
    clause: string | null;
    amount: Money;
    items: { name: string; qty: number; unit_price_paise: number }[];
  };
  approval?: ApprovalRequest | null;
  decision: Decision;
  protocol_version: string;
}

export interface AuthorizationState {
  authority: Authority;
  history: Record<string, unknown>[];
  protocol_version: string;
}

/** What `receipts.verify()` concluded, and on what basis. */
export interface ReceiptVerification {
  /** every check below passed */
  ok: boolean;
  /** the server's own hash-chain verdict for this trace */
  chain_intact: boolean;
  first_bad_seq: number | null;
  /** the verdict the evidence records */
  verdict: Verdict | null;
  /** checks this SDK ran locally, each with its result */
  checks: { name: string; passed: boolean; detail: string }[];
  /**
   * Always true, and stated rather than buried: the chain is hash-linked with
   * NO external trust anchor. It is tamper-EVIDENT against partial edits and
   * is not tamper-proof. Verifying it here proves the record is internally
   * consistent, not that the server is honest.
   */
  no_external_trust_anchor: true;
  evidence: Evidence;
}

/** Server description of itself, from `GET /v1/`. */
export interface ProtocolInfo {
  protocol: string;
  version: string;
  thesis: string;
  nouns: string[];
  routes: Record<string, string>;
  identity: string;
  notes: string[];
}
