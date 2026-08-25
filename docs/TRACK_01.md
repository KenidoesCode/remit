# Track 01 — AI Growth & Agentic Commerce

*Submission mapping. This document exists for the reviewer; the requirement
language is theirs, and each row points at the code, the test or the live
surface that satisfies it. Nothing here is a new capability — it is a map of
capabilities REMIT already has.*

Track 01 asks for money actions that are **explainable, bounded and gated**,
with an **audit trail** and **one failure handled gracefully**.

| Requirement | How REMIT satisfies it | Where |
|---|---|---|
| **Explainable** | Every decision produces an authorization receipt: the authority, the verdict, the clauses behind it, whether money moved, and how to verify it. | `remit/receipt.py` · `GET /v1/receipt/{id}` · `remit receipt show` · the audit room on the site |
| **Bounded** | A sentence compiles into an authority envelope — bounded in amount, time, category and actor. It is immutable and versioned. | `remit/domain/intent.py` · `docs/ARCHITECTURE.md` |
| **Gated** | A pure policy function of 21 clauses decides. It reads no free text, so a model cannot argue with it, and money moves only on `AUTO`. | `remit/policy/authorize.py` · `tests/test_policy.py` |
| **Audit trail** | A hash-linked event chain: `sha256(prev_hash + canonical({kind, trace_id, ts, payload}))`, recomputable by the client. | `remit/ledger/` · `docs/sdk/audit.md` |
| **Failure handled gracefully** | An out-of-authority proposal is denied or stepped up, no money moves, an audit event is written, and the receipt explains it. | `tests/test_receipt.py` · `tests/test_commerce.py` · the demo below |

## The one-flow demo

```bash
python demo/track01.py
```

It runs, against a live in-process instance with no API key:

1. **"buy a laptop under ₹50,000"**, agent proposes a laptop within budget →
   `AUTO`, a Razorpay **test-mode** order, and the authorization receipt.
2. The same authority, agent now proposes the laptop **plus a warranty and a
   bag** nobody asked for → REMIT **steps up or denies** according to the real
   policy, **no money moves**, and the receipt explains why.

The second step is the failure handled gracefully: the deterministic boundary
refusing an action the model was confident about, and leaving a verifiable
record of the refusal.

## What is *not* claimed

Per the rest of this repository, and repeated here so the mapping is honest:
Razorpay **test mode only**; the corpus is **synthetic and self-authored**; the
chain is **tamper-evident, not tamper-proof**; there is **no identity
provider**; and REMIT **does not win its own benchmark**. See
[`docs/LIMITATIONS.md`](LIMITATIONS.md) and
[`docs/FINAL_0_TO_100_AUDIT.md`](FINAL_0_TO_100_AUDIT.md).
