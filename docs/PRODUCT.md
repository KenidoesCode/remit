# Product

## Who it is for

**The buyer**, who says one sentence and then is absent while an agent makes
thirty decisions with their money.

**The merchant**, who wants a higher average order value from AI buyers and
cannot get it if buyers do not trust agents with a card.

**The PSP**, who is the only party that can see the whole journey and the only
one with the liability to care.

## The job

> Let AI optimise merchant revenue without giving it a blank cheque.

Concretely, REMIT is a constrained optimisation:

```
maximise    merchant margin
subject to  the final transaction stays inside the authorised intent envelope
```

This is a **product model**, not a universal mathematical standard, and it is
described that way everywhere.

## The user journey

1. **Say what you want.** "find me premium running shoes under ₹5000 and buy the
   best value option."
2. **The agent works.** Searches 101 products, ranks them deterministically
   against your stated objective, picks one, and explains why in a sentence you
   can check.
3. **The merchant gets a turn.** The revenue engine proposes accessories, each
   with a reason, an exact marginal cost, and whether it changes what you
   authorised. Nothing is added silently. Offers are accepted against a *running*
   total, so three that each fit cannot jointly break the envelope.
4. **The boundary is visible.** One bar: authorised, shown to you, about to be
   charged, room left. It moves from green to amber to a hatched red overflow.
5. **If the line is crossed, you are asked** — holding the actual number, with the
   clause that stopped it and the counterfactual that would have let it through.
6. **Money moves once.** Idempotent, reconciled, and written into a hash-chained
   Docket that can be replayed.

## What makes it different from an AI checkout

An AI checkout asks *can the agent complete a payment?* REMIT asks *does the
completed payment still represent what the human authorised?* — and then measures
the answer across 540 journeys and ten policy configurations.

The measurable claim, from `eval/experiments.py`: an agent optimising revenue with
no boundary earns real incremental revenue **and moves six figures of rupees
nobody authorised**. REMIT keeps 77% of that upside at exactly zero unauthorised
movement, with a *higher* average order value, because it attaches only what fits.

## Business hypothesis

Stated as assumptions, with no invented numbers.

- **What it is worth to a merchant:** higher AOV from AI buyers who are willing
  to grant an agent authority because the authority is bounded and visible. The
  measured AOV lift over plain checkout in the corpus is real within the corpus
  and is not a forecast.
- **What it is worth to a PSP:** disputes that arrive with an evidence bundle
  rather than a shrug, and a defensible answer to "who authorised this?".
- **How it would be productised:** as agent controls in a merchant dashboard, or
  as the authority module of whatever agent protocol standardises around the PSP.
- **What is unknown:** the real rate of intent-to-transaction mismatch in
  production. Nobody has published it. That is the number this system is built to
  produce, and the reason the harness matters more than the demo.
