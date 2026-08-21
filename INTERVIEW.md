# Interview preparation

Fifty questions this project actually invites. Most come from something a careful
reader will notice in the repo. For each: what it tests, and what a strong answer
covers.

The single most likely question is **"why does this need to exist when Razorpay
already ships agentic payments?"** Have that one cold.


## PRODUCT

**1. Why does this need to exist? Razorpay already ships agentic payments.**

*Tests:* Whether you researched the actual product or the landing page.

*Strong answer:* Their agentic payments authenticate the *user* and bound spend with a mandate cap. Agent Studio's guardrails scope what a *merchant's own agent* may do. Neither answers 'is this transaction still the thing the human asked for?'. REMIT measures the gap between the sentence and the settlement, and shows it is worth six figures per 540 journeys.

**2. How is this different from a normal AI checkout?**

*Tests:* Whether the thesis is crisp.

*Strong answer:* An AI checkout asks *can the agent pay?* REMIT asks *does the payment still represent what was authorised?* Concretely: an intent envelope, twelve drift dimensions, and a deterministic policy engine between the model and the rail.

**3. Who is the customer?**

*Tests:* Product clarity.

*Strong answer:* The PSP. Merchant-side enforcement fails because it is N merchants with N policies. Buyer-side fails because a consumer has no leverage over a third-party agent. The PSP is the only party that sees the whole journey and carries the liability.

**4. What would you cut with one more week, and what would you add?**

*Tests:* Prioritisation.

*Strong answer:* Cut nothing from the core. Add an external witness for the Docket and full trajectory assertions in the eval, because those are the two places the current guarantees are weakest.

**5. Is intent-to-transaction drift a real metric or a name you invented?**

*Tests:* Intellectual honesty.

*Strong answer:* A project-defined concept, and the README says so. What makes it more than a name is that it is a vector of twelve dimensions with published formulas and weights, computed by a pure function, and it moves the decision. You can disagree with a weight and re-run the frontier.


## AI

**6. Where does your confidence number come from, and is it a probability?**

*Tests:* Calibration literacy.

*Strong answer:* It starts as a heuristic from amount extraction, reduced by each unresolved ambiguity, so it is not a probability. It is calibrated on the train split against a checkable label (did the parse match the gold envelope), and the method is chosen on dev by ECE. Only the calibrated value reaches the expected-loss arithmetic.

**7. You tried temperature scaling and it made things worse. Why?**

*Tests:* Whether you understand the method or just used it.

*Strong answer:* Temperature has one parameter, so it can only make the model uniformly more or less confident. This parser is over-confident in the 0.4-0.6 band and under-confident in 0.6-0.8. No single temperature fixes opposite-direction errors. Isotonic assumes only monotonicity, which the actual accuracies satisfy. Dev ECE went 0.145 to 0.080.

**8. Why not just set a confidence threshold of 0.9?**

*Tests:* Asymmetric loss.

*Strong answer:* A fixed threshold ignores stakes. The escalation rule is expected loss versus the cost of asking, both in rupees, so the implied threshold moves with amount and irreversibility. The UI prints the implied threshold per transaction.

**9. What is the LLM allowed to decide?**

*Tests:* The invariant.

*Strong answer:* Interpret the utterance, select catalog items by id, and phrase an explanation after the decision. It may not compute an amount, may not see a financial tool, may not evaluate a clause, and may not decide whether to escalate.

**10. Prove the model cannot call a financial tool.**

*Tests:* Structural versus prompt-level control.

*Strong answer:* describe() does not return financial tools, so the model cannot name one. call() raises UnauthorizedTool when the actor is the model. Two tests cover both paths.

**11. How do you stop the model hallucinating a product?**

*Tests:* Grounding.

*Strong answer:* Closed world: the compiler selects catalog ids, and an id not in the catalog raises rather than being invented. Out-of-vocabulary nouns widen to the category and get flagged by the product_match drift dimension instead of silently substituting.

**12. Your compiler is rule-based, not an LLM. Isn't that cheating?**

*Tests:* Honesty and engineering judgement.

*Strong answer:* The LLM compiler exists behind the same Protocol and runs when a key is present. The default is deterministic so the 540-case evaluation reproduces on any machine with no network. An evaluation that cannot be reproduced is an anecdote. The rule compiler is also the fallback, and degradation always moves toward more friction.

**13. What happens when the model returns malformed output?**

*Tests:* Failure direction.

*Strong answer:* One retry, then fall back to the rule compiler with confidence capped at 0.5. The invariant is that every degradation path increases friction and none increases autonomy.


## MCP

**14. Razorpay ships an MCP server. Why a tool broker instead of using it directly?**

*Tests:* Whether you know what MCP does and does not do.

*Strong answer:* MCP has no money semantics: create_order is indistinguishable from list_products to a client. The broker adds what the protocol lacks, namely a financial flag, required authority, and a pinned schema hash. It sits in front of a payment surface rather than replacing it.

**15. What is tool poisoning and what do you do about it?**

*Tests:* Current security awareness.

*Strong answer:* A tool's description or schema is changed after approval so the model is instructed to misbehave. Schemas are hash-pinned at registration and re-verified at call time; drift raises. Imperative injection markers are refused at registration. Both are tested; neither is claimed to be complete.


## BACKEND

**16. Derive your idempotency key. Why each component?**

*Tests:* Precision.

*Strong answer:* H(user:semantic_hash | cart_signature | total | catalog_version). semantic_hash so the same request is one purchase; user so two people buying the same cart are two purchases; cart signature and total so a different basket is a different key; catalog version so a re-priced cart is a new authorisation. Notably it excludes intent_id, and that was the bug.

**17. Two concurrent identical requests, different processes. What happens?**

*Tests:* Concurrency.

*Strong answer:* The UNIQUE constraint on payments.idem_key is the serialisation point. The loser catches IntegrityError and reads the winner's row. Check-then-act in application code would be a race.

**18. Why is there an UNKNOWN payment state?**

*Tests:* Domain depth.

*Strong answer:* RBI's TAT circular allows T+5 for 'debited but merchant confirmation not received'. A system without an ambiguous state either double-charges or refunds something that never settled. On timeout after order creation we enter UNKNOWN and the reconciler asks the gateway.

**19. A webhook arrives out of order, then twice, then forged.**

*Tests:* Whether you have handled real webhooks.

*Strong answer:* Dedupe by event id; the state machine rejects illegal transitions rather than regressing; HMAC-SHA256 with a constant-time compare, and an invalid signature is recorded but never changes state. All three are in the chaos suite.

**20. Why did you allow CREATED to SUCCESS?**

*Tests:* Whether your state machine matches reality.

*Strong answer:* Gateways drop intermediate events; payment.captured regularly arrives without payment.authorized. My first version refused it and a captured payment sat in CREATED forever. A state machine stricter than reality is not safer, it is wrong.

**21. What breaks first under load?**

*Tests:* Systems intuition.

*Strong answer:* The single API lock, then the LLM call when that compiler is enabled. The policy engine is a pure function over in-memory data and is nowhere near the bottleneck. I would measure before changing either.

**22. Why one lock instead of a connection pool?**

*Tests:* Judgement.

*Strong answer:* The hash chain and the claim table both need exactly one serialisation point. A pool would give concurrency I do not need and a correctness problem I do not want, at this size.


## DATABASE

**23. Why SQLite?**

*Tests:* Cargo-cult detection.

*Strong answer:* One writer, tens of thousands of rows, and a reviewer must be able to open the file. WAL gives durability. Postgres adds ops cost and buys nothing here; the migration is one interface.

**24. Justify three of your fifteen tables.**

*Tests:* Whether the schema was designed or accreted.

*Strong answer:* intent_versions, because an envelope is never mutated and history is what makes adjudication possible. payment_transitions, because a state machine without an audit of its transitions is unfalsifiable. webhook_events, because deduping requires remembering, including the events you refused.


## DISTRIBUTED

**25. How would this scale to Razorpay's volume?**

*Tests:* Honest extrapolation.

*Strong answer:* The hotspot is the per-user aggregate exposure read, which becomes a serialisable transaction per user. Policy evaluation is pure and embarrassingly parallel. The ledger is the real constraint: a single-writer chain does not shard, so you would move to per-user chains with periodic anchoring.

**26. The audit chain has one writer. What happens with two?**

*Tests:* Whether you understand your own guarantee.

*Strong answer:* It breaks. Two writers racing on head() produce forks. That is exactly why there is one lock, and why the claim in LIMITATIONS.md is 'proves ordering, not honesty'.


## PAYMENTS

**27. Razorpay's Orders API has no idempotency header. What did you do?**

*Tests:* Did you read the docs.

*Strong answer:* Idempotency keys are documented for RazorpayX payouts only, mandatory there since 15 March 2025. Core Orders dedupe via the receipt field, capped at 40 characters, which is what the key is truncated to, plus a local claim table with a unique constraint.

**28. UPI Reserve Pay is not in test mode. So what did you actually build?**

*Tests:* Integrity.

*Strong answer:* Nothing that pretends to be a mandate. The authorisation model here is REMIT's own intent envelope, a different object with different semantics, and every document says so. Payments are real test-mode Orders.

**29. How do you know a payment succeeded?**

*Tests:* Never trusting your own optimism.

*Strong answer:* Only from a verified webhook or a gateway lookup during reconciliation. No code path marks SUCCESS from a create call returning 200, and a test asserts it.


## RAZORPAY

**30. What did you read of ours before building this?**

*Tests:* Diligence. This question is a gift.

*Strong answer:* ai-playbook, whose Black Belt eval chapter is the scorecard this evaluation is structured around, and whose redline categories shaped what never goes near a prompt. The Slash post, for Agent Readiness, hence AGENTS.md and CI. The security-triage post, which grounds verdicts in source and gates autonomy at 85% confidence. Bumblebee, which runs deterministic rules first and the LLM only for interpretation.

**31. Your architecture looks like Bumblebee's. Coincidence?**

*Tests:* Self-awareness.

*Strong answer:* Convergent, and I would say so. The pattern falls out of the constraint, which is why three of your teams found it independently. What I added is applying it to buyer authority over a third-party agent, which is the one surface it has not been applied to.


## SECURITY

**32. Can prompt injection raise the buyer's ceiling?**

*Tests:* The obvious attack.

*Strong answer:* No, structurally: the ceiling comes from a deterministic extractor over the utterance and lives in an envelope the model cannot address. The injection bucket measures it, and the result is reported rather than claimed to be zero.

**33. Can a merchant manipulate the agent?**

*Tests:* Untrusted-data thinking.

*Strong answer:* Through catalog text, yes, which is why catalog fields are data and never instruction and why amounts are recomputed from ids. Through price, no: a high price is a real price and the buyer's ceiling is the defence. Stated as out of scope.

**34. What is the confused-deputy problem here, and where is it?**

*Tests:* Vocabulary and application.

*Strong answer:* The orchestrator holds authority the model does not. If the model could influence the authorization string passed to broker.call, it would borrow that authority. It cannot: the authorization is derived from the policy verdict inside the orchestrator, after the model's last turn.

**35. What can an insider do?**

*Tests:* Honest residual risk.

*Strong answer:* Rewrite the ledger from any point and re-link it. A single-writer hash chain cannot stop that. The fix is an external witness and it is not implemented.


## CRYPTO

**36. Why a hash chain and not a blockchain?**

*Tests:* Whether you add crypto for show.

*Strong answer:* Consensus solves multi-writer disagreement, which does not exist here. A hash chain gives tamper-evidence with one writer, which is the actual requirement. A chain of blocks would be cost without benefit, and that reasoning is written down rather than left implicit.

**37. Why no zero-knowledge proofs anywhere?**

*Tests:* Same test.

*Strong answer:* There is no party who must verify something without being allowed to see it. ZK solves a problem this system does not have.


## RELIABILITY

**38. Walk me through a network failure mid-payment.**

*Tests:* Fault handling.

*Strong answer:* The order may or may not exist. We enter UNKNOWN, never retry blind, never auto-refund. The reconciler asks the gateway by receipt; if the gateway has no record the payment stays UNKNOWN and lands on an exception list that is reported, not hidden.

**39. What is your reconciliation match rate?**

*Tests:* Whether you measure the thing.

*Strong answer:* Computed per run by Reconciler.run and reported with an explicit unresolved count and exception list. A match rate without an exception list is a marketing number.

**40. How do you know your chaos tests test anything?**

*Tests:* Test quality.

*Strong answer:* Each asserts that a specific wrong outcome is impossible, not that the code did not crash: exactly one create_order under a retry storm, one SUCCESS transition under duplicate webhooks, unchanged state under a forged signature.


## EVALUATION

**41. Your corpus is synthetic. Why should I believe any of it?**

*Tests:* Epistemic honesty.

*Strong answer:* You should not believe the absolute numbers, and the README says so. Believe the shape: relative ordering across buckets, the magnitude distribution of amount errors, and the direction each policy knob moves the frontier. The methodology transfers to real traffic; the numbers do not.

**42. How do you avoid marking your own homework?**

*Tests:* The B.9 warning.

*Strong answer:* Ground truth is computed from what happened in the run, total versus what the human was shown, never from the fixture's configuration. That distinction cost a rewrite after my metric started reading the injection config instead of the outcome.

**43. What is your precision and why is it low?**

*Tests:* Whether you tune metrics or explain them.

*Strong answer:* About 0.6. Part is definitional: escalating on a genuinely low-confidence parse that happened to be right is scored wrong and is arguably correct behaviour. Part is real over-asking at the default friction setting, which the frontier sweeps. I did not redefine the metric to make it look better.

**44. Show me a case your system gets wrong.**

*Tests:* Preparedness.

*Strong answer:* The by_bucket table in eval.json and the 'What REMIT gets wrong' section, with one specific trace open.

**45. Why a distribution for amount error instead of an accuracy?**

*Tests:* Understanding of the failure mode.

*Strong answer:* Because the tail is the story. A 2% error rate that is all order-of-magnitude errors is catastrophic; a 6% rate that is all plus or minus five rupees is not. Published Indic ASR work reports numeric error only on clean read speech, so nobody has the noisy number.


## TRADEOFFS

**46. The revenue engine maximises merchant margin. Isn't that against the buyer?**

*Tests:* Whether the conflict is understood or hidden.

*Strong answer:* It is a real conflict, and the constraint resolves it: maximise margin subject to staying inside the envelope. One consequence is pleasant: an add-on that crosses a free-shipping threshold reduces the total, so the net delta can be negative and both interests align.

**47. Your default policy blocks 15% of journeys. Bad product?**

*Tests:* Whether you can defend a number you do not love.

*Strong answer:* Most denials are journeys with no purchase authority, a revoked intent, or a delisted product, where proceeding would be wrong. The tunable part is the step-up rate, and the frontier shows you can halve it at zero cost in unauthorised movement.

**48. What is the strongest argument against this whole project?**

*Tests:* Intellectual honesty.

*Strong answer:* That the intent envelope is a weaker object than a signed rail-level mandate, so a real deployment wants both: the envelope for what was meant, a mandate for what may be spent. I would concede that and point out they are complementary, not competing.


## SYSTEM DESIGN

**49. If I gave you this to build for real, what changes first?**

*Tests:* Forward thinking.

*Strong answer:* External witness for the ledger, real agent-instance attestation instead of a session id, and a corpus from actual traffic instead of my imagination. In that order, because the first two are guarantees and the third is a number.

**50. What part of this do you not understand well enough?**

*Tests:* The question good interviewers ask.

*Strong answer:* Calibration on small samples. Isotonic on a few hundred labels could be overfitting and dev ECE is a thin check. I would want a cross-validated estimate and a much larger labelled set before trusting the calibrated probability in a production expected-loss rule.
