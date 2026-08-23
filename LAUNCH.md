# Launch

Copy for every surface. Written to be used as-is, or edited freely — but the
constraints are not decoration:

- **No endorsement.** Razorpay test mode is an integration, not a relationship.
- **No hiring ask.** The work is the argument. Asking cheapens it.
- **No invented numbers.** Every figure below is in `docs/FINAL_BASELINE.md`
  and regenerates from `python verify.py`. If a number here disagrees with that
  file, the file is right.
- **The ask is star / break / contribute**, in that order of ease and reverse
  order of value. An attack that works is worth more than a star.

Real links, all verified:

| | |
|---|---|
| Live | https://remit-vvug.onrender.com |
| npm | https://www.npmjs.com/package/remit-sdk |
| Source | https://github.com/KenidoesCode/remit |
| Docs | https://github.com/KenidoesCode/remit/tree/main/docs/sdk |

---

## Headline

**REMIT — model-independent authorization for autonomous commerce.**

> AI can be probabilistic. Authorization cannot.

## One line

The authorization boundary between autonomous agents and financial action.

## Short (GitHub description, 120 chars)

> Model-independent authorization for autonomous commerce. AI proposes, REMIT authorizes, the payment rail executes.

## npm description

> The authorization boundary between autonomous agents and financial action.
> Bring your own model; REMIT decides what it is allowed to do.

## Long

An AI agent can search, compare, decide and pay. But a model output is not an
authorization, and a spending limit is not one either — a limit can only ask
*how much*, never *is this the thing the human actually asked for*.

REMIT is that missing layer: a deterministic authorization boundary that sits
between an agent's proposal and a payment rail. The agent interprets. A pure
policy function decides whether the action is still inside what a human
authorised. Only then does money move, and the whole decision is reconstructable
afterwards from a hash-linked record.

Open source, MIT, with a TypeScript SDK on npm and a CLI.

---

## LinkedIn

> **A spending limit is not an authorization.**
>
> I spent the last few weeks on a question that kept bothering me about agentic
> commerce: an AI agent can now search, compare, decide and pay — but what
> exactly is it allowed to buy?
>
> The usual answer is a spending limit. So I tried to break that idea.
>
> Say you tell an agent: *"buy a laptop under ₹50,000."* The best match in the
> catalog is a laptop **stand**, at ₹4,446. A spending limit approves it
> instantly — it is well under ₹50,000, and "how much" is the only question a
> limit can ask.
>
> That gap is the entire project.
>
> **REMIT** is a deterministic authorization boundary between an agent and a
> payment rail. The agent interprets; a pure policy function — 21 clauses, no
> I/O, no network, and it reads no free text at all — decides whether the action
> is still inside what a human authorised. Only then does money move.
>
> The part I did not expect to be the interesting part: what broke.
>
> · A process restart re-keyed idempotency and **charged twice on redeploy**.
> · Six processes **forked the audit chain permanently** — a threading lock had
>   been hiding it for weeks.
> · Two tenants collided on one payment; the second was told "replayed", handed
>   the first one's order, and given nothing.
> · A race **inside** the code I wrote to fix races. One in three, in the money
>   path, found by running the attack suite one more time than I needed to.
>
> All 53 of those are written down in FAILURES.md, with the regression test
> underneath each one. Not sanitised. That file is the part of the repo I would
> read first.
>
> It also does not win its own benchmark, and I left that in: a "buy the
> cheapest thing" agent scores higher, because REMIT sometimes buys the wrong
> thing rather than letting money escape. The agent that earned the *most*
> revenue moved ₹737,930 nobody authorised.
>
> It is open source and installable:
>
> `npm install remit-sdk`
>
> Live demo, and a Break REMIT page that runs the real security path rather than
> an animation: remit-vvug.onrender.com
>
> Built against Razorpay test mode — real orders, no real money. Not affiliated
> with or endorsed by Razorpay; this is my own exploration of a problem their
> space is about to have.
>
> If the idea is interesting: star it, break it, or open an issue. An attack
> that works is worth more to me than a star.
>
> #AgenticCommerce #AIAgents #Fintech #OpenSource #Authorization

---

## X / Twitter

**Thread opener**

> A spending limit is not an authorization.
>
> Tell an AI agent "buy a laptop under ₹50,000". The closest match is a laptop
> STAND at ₹4,446.
>
> Every spending limit on earth approves that. It's under the number.
>
> That gap is why I built REMIT. 🧵

**2/**

> REMIT is the authorization boundary between an agent and a payment rail.
>
> The agent interprets. A pure function — 21 clauses, no I/O, no network, reads
> no free text at all — decides if the action is still inside what a human
> authorised.
>
> Only then does money move.

**3/**

> The model can't talk it into anything, because the decider can't read.
>
> If an LLM returns {"authorized": true}, the server strips 13
> authorization-shaped fields and *records that it tried*.
>
> Bring your own model. GPT, Claude, Gemini, local Llama. Same boundary.

**4/**

> What actually broke, all in FAILURES.md:
>
> · restart re-keyed idempotency → double charge on redeploy
> · 6 processes forked the audit chain permanently
> · two tenants collided on one payment
> · a race inside the code I wrote to fix races
>
> 53 entries. None removed for being embarrassing.

**5/**

> It doesn't win its own benchmark and I left that in.
>
> A "buy the cheapest thing" agent scores higher.
>
> But the agent that earned the MOST revenue moved ₹737,930 nobody authorised.
>
> Revenue isn't the metric.

**6/**

> Open source, MIT, TypeScript SDK + CLI:
>
> npm install remit-sdk
>
> Live demo + a Break REMIT page that runs the real security path:
> remit-vvug.onrender.com
>
> Star it, break it, or open an issue 👇
> github.com/KenidoesCode/remit

---

## Instagram Reel — 40 seconds

| Time | On screen | Voiceover |
|---|---|---|
| 0:00 | Black. `REMIT` types in. | "What if I built the thing underneath Razorpay's agentic commerce problem…" |
| 0:04 | Terminal: `"buy a laptop under ₹50,000"` | "You tell an AI agent to buy a laptop under fifty thousand." |
| 0:09 | Product card: **laptop stand — ₹4,446** | "It finds a laptop *stand*. Four thousand rupees." |
| 0:13 | Big text: **UNDER ₹50,000 ✓** | "Every spending limit on earth says yes. It's under the number." |
| 0:17 | Red: **`STEP_UP · MATCH-001`** | "REMIT says no. A modifier match is not a product match." |
| 0:22 | Split: AGENT / REMIT / RAZORPAY | "I didn't want to build another AI agent. I wanted to build the thing that decides what an agent is *allowed* to do." |
| 0:27 | Razorpay test-mode order, then receipt ✓ | "Test mode. Real orders, no real money. Every decision leaves a receipt you can verify yourself." |
| 0:31 | `npm install remit-sdk` | "It's open source, and it installs." |
| 0:34 | Break REMIT page, attacks turning red | "There's a page where you can try to break it." |
| 0:38 | **AI CAN BE PROBABILISTIC.**<br>**AUTHORIZATION CANNOT.** | "AI can be probabilistic. Authorization can't." |

**Caption**

> A spending limit can only ask *how much*. It can't ask *is this the thing you
> asked for*. So I built the layer that can.
>
> `npm install remit-sdk` — open source, MIT. Link in bio.
>
> Built against Razorpay test mode. Not affiliated with Razorpay.
>
> Try to break it. Seriously — that's the fun part.

## Reel — 15 second cut

| Time | On screen | Voiceover |
|---|---|---|
| 0:00 | `"buy a laptop under ₹50,000"` | "You say: buy a laptop under fifty thousand." |
| 0:04 | **laptop stand — ₹4,446** | "The agent buys a laptop *stand*." |
| 0:07 | **UNDER ₹50,000 ✓** | "A spending limit approves it." |
| 0:10 | Red **`MATCH-001`** | "REMIT doesn't." |
| 0:13 | `npm install remit-sdk` | "Open source. Go break it." |

---

## Technical post (dev.to / Hashnode)

**Title:** *Your AI agent has a payment key. What is it actually allowed to buy?*

**Outline**

1. The setup — agents can pay now; a model output is not an authorization.
2. The counterexample — laptop → laptop stand, ₹4,446, under every limit.
3. Why a limit cannot fix this — a limit has one input, and it is a number.
4. Why an LLM judge cannot fix it either — two persuadable systems in series.
5. The design — an authority envelope; a decider that reads no text.
6. `integrity_layer: true/false` — with and without REMIT as a **data** change,
   not a code path, so the comparison cannot be rigged.
7. Multi-process reality — what six real processes did to a hash chain.
8. Idempotency keyed on **meaning** rather than an id.
9. Receipts you verify client-side, and the float literal that made that hard.
10. What it costs: 27.3 µs, and the honest 0.6346 precision.
11. What is still missing, named.

## Security post

**Title:** *32 attacks against my own payment authorization layer, and the six that worked*

Lead with the six that worked. `FAILURES.md` #37, #38, #45, #47, #48, #50 — a
demo endpoint that mutated the live catalog, a replay path with zero exposure, a
double charge on redeploy, a permanently forked audit chain, a silent
cross-tenant leak, and a race inside the race-fix.

Close on the two things local verification **cannot** prove: there is no
external trust anchor, and the corpus was written by the author.

## SDK announcement

> `npm install remit-sdk`
>
> The REMIT authorization protocol, as a typed client. Zero runtime
> dependencies, ESM + CJS, and a CLI.
>
> No API key — a bearer key lets a caller choose whose limits to spend.
> No idempotency key — REMIT derives it from the *meaning* of the request, so
> anything you passed would be weaker.
>
> `receipts.verify()` recomputes every audit hash locally instead of repeating
> the server's claim about itself. It will not return ok:true for a check it
> could not run.
>
> github.com/KenidoesCode/remit

---

## Content series

1. **Why spending limits aren't enough for AI agents** — the laptop stand.
2. **The worst bug I found in REMIT** — six processes, one forked hash chain,
   and the threading lock that hid it.
3. **How model-independent authorization works** — why the decider reads no text.
4. **I turned REMIT into an SDK** — and publishing it deleted my own CLI
   (`FAILURES #54`).
5. **Try to break REMIT** — walk through the attack surface.
6. **What I learned building financial authorization infrastructure** — the
   numbers in the prose had stopped being true, and a test now reads them.

Each teaches one thing. None is an announcement.

---

## The line to end on

> **REMIT**
> AI can be probabilistic. Authorization cannot.
>
> Build it. Break it. Verify it.
