# Creative DNA

## The one sentence

> **The friendly neighbourhood developer built a system serious enough to trust
> with money.**

Everything below exists to earn that sentence. If a design decision does not
serve either half of it — *friendly neighbourhood* or *serious enough to trust
with money* — it gets cut.

## The contrast is the brand

Not "friendly" and not "serious". **Friendly AND serious, at the same time, in
the same object.** A reviewer should feel the whiplash: warm, low, conversational
copy sitting directly on top of a policy engine with seventeen clause IDs and a
tamper-evident hash chain.

The failure mode to avoid is picking one. A cute site with a serious repo reads
as a student who can't judge tone. A serious site with a cute footer reads as a
site with a cute footer.

## Where the personality lives, and where it does not

| lives here | never here |
|---|---|
| the opening title card | the verdict panel |
| empty states | anything about a payment failing |
| the failure lab | security claims |
| debug mode | the evaluation numbers |
| microcopy on non-critical actions | drift and risk explanations |
| the closing line | error messages the user must act on |

**Rule:** the moment real money is in the sentence, the voice goes flat and
precise. Humour before the decision and after the decision, never during it.

## The neighbourhood

The central original metaphor, and it is not decoration — it is the intent graph,
drawn.

- **The home** is the human. Everything starts there.
- **The property line** is the intent boundary. What was authorised.
- **The neighbourhood** is the merchant catalog. Where the agent may wander.
- **The route** is the journey: search, select, offer, cart, policy, payment.
- **The destination** is the payment.
- **When the agent crosses the property line, REMIT stops it at the line.**

This is why "friendly neighbourhood developer" is not a costume. The product's
core concept genuinely *is* a boundary around a home, and an agent that may
explore freely inside it and must knock before leaving.

Threads and connections, not spiderwebs. Routes and property lines, not houses.
The motif is a **network with an edge**, drawn as thin lines with one hard
boundary — original, and it renders the real data.

## Colour

Blue is the brand. It is not the semantics.

| token | role |
|---|---|
| **deep blue** `#0B1party` → the ground | system, authority, the thing that holds |
| **electric blue** | the agent, motion, intent, action |
| **pale blue** | discovery, information, the moments of play |
| green | *inside the line* — semantic only |
| amber | *near the line* — semantic only |
| red | *over the line, refused* — semantic only |

**Brand colour and status colour are never the same colour.** A dashboard where
the accent is also the success state is a dashboard you cannot read at a glance.
This is the single most common way "distinctive blue" becomes "generic fintech".

The blues are deliberately cold and slightly green-shifted rather than the
purple-leaning blue every AI product ships. The neutrals carry a trace of the
same shift so the greys read as *chosen*.

## Type

- **Display:** a serif with actual voice, used only for act titles and the
  opening. Warmth, and a hint of the cinematic.
- **Interface:** a neutral grotesque, not Inter.
- **Data:** a monospace with tabular figures, used for every number, every clause
  ID, every hash. Money is always monospace. This is a fintech tell and it is
  worth having.

## Motion

Motion means **state**, never delight.

The one animation that matters is the transaction moving toward the property
line. Everything else is a fade or a small translate. Nothing bounces. Nothing
spins. `prefers-reduced-motion` removes all of it and the product is complete
without it.

## The number five

Not sprinkled. It appears twice, both times because it was already true:

1. **Five acts.** The experience is a five-act structure.
2. **Five payment states.** `CREATED · AUTHORIZED · SUCCESS · FAILED · UNKNOWN` —
   the state machine has exactly five, and it had five before anyone thought
   about this.

Never explained. A curious reviewer notices; nobody else does.

## Speed

The cheetah becomes a number, not a picture. Decision latency is displayed in
the interface as a live figure, in milliseconds, from the real request — because
"the policy engine is pure and therefore fast" is a claim the product should be
able to *show*, not assert.

## What this is not

Not a portfolio. Not a resume page. Not a Marvel fan site. Not an energy-drink
advertisement. Not a hacker terminal. Not a landing page with a gradient hero and
three feature cards.

It is a **product**, with a person visible behind it.
