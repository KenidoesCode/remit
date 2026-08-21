# The experience: five acts

A film, not a set of pages. Each act answers one question and hands the next act
a reason to exist.

---

## ACT 0 — The title card (8 seconds, skippable)

Near-black deep blue. One thin line of light — a single thread — draws itself
across the screen.

```
                    hey.
              i'm techuilaguy.
      your friendly neighbourhood developer.

   today i built something that lets AI spend money.
        and then refuses to let it spend yours.

                   R E M I T
```

Then the thread becomes the property line, and the product is behind it.

**Rules:** skippable with any key or click, skipped automatically under
`prefers-reduced-motion`, never shown twice in a session, and the product is
fully usable if it never plays at all.

**Why it earns its place:** it establishes the contrast in eight seconds — a
friendly lowercase greeting, immediately followed by a sentence about money and
refusal. That is the whole brand, delivered before anything is explained.

---

## ACT I — The neighbourhood *(question: where am I?)*

The reviewer sees the map, not a dashboard. A quiet network: **home** on the
left, the **merchant neighbourhood** as a field of nodes, the **destination**
on the right, and a hard vertical **property line** between exploration and
payment.

One input. One sentence. Nothing else.

> *buy premium running shoes under ₹5000 and get the best value one*

---

## ACT II — The agent moves *(question: what does it do?)*

The route draws itself across the neighbourhood, node by node, as the real
journey executes: search → select → offers → cart. Each node carries the actual
payload from `/api/graph`.

Then the merchant's turn: the revenue engine proposes, each offer with a reason
and an exact marginal cost. Nothing is added silently. The route creeps toward
the property line as the basket fills.

The reviewer watches the agent get **closer to the line** — and stop just inside
it. ₹4,998 of ₹5,000.

---

## ACT III — The line *(question: what happens when it doesn't fit?)*

**This is the signature interaction. Internally: the Property Line.**

The boundary is a draggable object. The reviewer takes the human's authority in
their hand and moves it.

- Drag it up: the agent's route relaxes, offers get accepted, the verdict goes
  green.
- Drag it below the basket: the route hits the line and **stops at it**. Clauses
  flip red one by one. The verdict changes to STEP_UP or DENY. The money does not
  move.

Every drag re-runs the **real policy engine** — pure, no I/O, no model call — and
the latency is displayed. Sub-millisecond, live, on screen.

**Why this is the moment:** the reviewer is physically holding a human's
authorisation and watching an AI obey it in real time. It connects intent,
money, boundary and the person in one gesture. And it *proves* the architectural
claim — you can only re-decide 540 recorded journeys instantly if the decision
function is genuinely pure.

---

## ACT IV — Break it *(question: is it actually robust?)*

> **go on. try to break it.**

A panel of real levers, each wired to a real code path:

change the price · change shipping · delist the product mid-journey · revoke the
intent · expire the intent · inflate the quantity · inject a prompt · fire a
duplicate webhook · fire an out-of-order webhook · forge a webhook signature

REMIT responds live: the clause that caught it, the drift dimension that moved,
the state the payment landed in. Nothing is scripted; every lever calls the
system.

Then, beside it, the comparison: **the same journey with the boundary switched
off**. Same user, same merchant, same agent, permissive policy. Revenue goes up.
So does the money nobody authorised.

---

## ACT V — The engineer *(question: who did this?)*

Now, and only now, the depth.

**The numbers** — four arms, 540 journeys, the gates, the frontier.

**stuff i broke** — the ten real failures from `FAILURES.md`, in his words, with
the fix and the lesson. Including the one where his own drift engine scored a
completely wrong purchase as a perfect match.

**how this guy builds** — build, break, fix, ship. CodeNex. DayZero. Twenty days
for a full edtech platform. And one honest line about the Spotify clone he
regrets, because following someone else's blueprint isn't really his thing —
which is exactly why REMIT is not a clone of anything.

**The close.** Quiet. One line. Not a thank-you, not a pitch.

---

## The pacing rule

Act 0 is 8 seconds. Acts I–III are the product and should take 90 seconds to
understand without reading a word of documentation. Act IV is where a curious
reviewer loses ten minutes. Act V is for the one who is already convinced and
wants to know if the person is real.

A reviewer who bounces after Act III should still have understood the entire
product.
