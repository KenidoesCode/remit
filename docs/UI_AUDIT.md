# REMIT — UI audit

*Read before changing a pixel. This is a refinement pass, not a redesign: the
identity stays, the hierarchy gets fixed.*

---

## First, a correction to the brief

The brief refers to "the existing ASCII fire component" with a warm
orange/yellow palette and asks for its palette to be replaced.

**There is no ASCII fire component in this repository.** Nothing renders
characters, and no library is involved. What exists behind the type is two
layers:

- `web/gl.js` — hand-written WebGL1, ~2 shaders and 2 buffers, drawing a field
  of drifting points and the threads between them. It is a metaphor for the
  merchant neighbourhood, not decoration. Its colour comes from one uniform and
  it is already `--signal` red.
- `#glow` in `web/style.css:36-62` — three blurred radial gradients plus a
  darkening overlay. **This is the layer the brief is actually describing.**

I am applying the *intent* of that instruction to the layer that exists rather
than inventing the component it names. The instruction is right about the
symptom: the background currently competes with the typography, and one of its
stops is orange.

---

## Current design system

**Palette** — `web/style.css:6-21`

| token | value | role |
|---|---|---|
| `--bg` | `#0B0908` | near-black ground |
| `--bg-2` | `#0F0C0B` | raised surface |
| `--ink` | `#FFFFFF` | primary type |
| `--ink-2` | `rgba(255,255,255,.66)` | body |
| `--ink-3` | `rgba(255,255,255,.42)` | secondary |
| `--ink-4` | `rgba(255,255,255,.24)` | metadata |
| `--line` / `--line-2` | 11% / 6% white | rules and grids |
| `--signal` | `#E5352B` | the red. Accent only |
| `--stop` | `#FF3B2F` | failure state |

No blue, no purple, no green. Correct and unchanged.

**Typography** — two families, both loaded, both used:
`--mono` JetBrains Mono (labels, numbers, clause ids, metadata) and `--sans`
Instrument Sans (headings, prose). The system is: **mono means machine-written,
sans means human-written.** That rule is good and mostly held.

**Spacing** — `clamp()` throughout, `--pad: clamp(20px,5vw,64px)`,
`--maxw: 1320px`. Section rhythm comes from `.act` padding and `.act-head`.

**Components** — `.act` / `.act-head` / `.kicker` / `.lede` · `.stat` cards ·
`.tw > table` · `.badge` (AUTO/STEP_UP/DENY) · `.clause` grid · `.dim` chips ·
`.vs` split · `.offer` · `.lever` · `.stepup` · `.atk` cards · `.chips`.

**Animation** — GSAP + ScrollTrigger, with a no-op shim so a missing CDN lands
every tween on its end state. The opening is a timeline with a `setTimeout`
backstop (FAILURES #15).

---

## Problems, ranked

### P1 — The background competes with the type

```css
#glow .g1{ ... rgba(229,53,43,.62) 0% ... }   /* 62% red, 78vw wide */
#glow .g2{ ... rgba(255,86,40,.26) 0% ... }   /* ORANGE */
```

Two things at once. `rgba(255,86,40)` is an orange — the brief bans it and it
does not belong in a black/red system. And `.g1` at `.62` over a 78vw circle is
why the hero photographs as a red wash: the eye lands on the light before it
lands on the wordmark.

**Fix:** re-cut both stops to the crimson ramp, drop `.g1` to roughly a third
of its current intensity, and push the energy outward so the centre stays calm.
Target: the background is *noticed second*.

### P1 — The Arena table is nine columns wide, one of which is a paragraph

`renderArena()` renders rank, agent, thesis, score, economic value,
unauthorised, trust, autonomy, asked — and the thesis is a full sentence inside
a table cell. It overflows the viewport (visible in the screenshots) and the
actual finding — *the frugal agent beat the growth hacker* — is buried in
column three of row one.

**Fix:** headline metrics first (who won, REMIT score, unauthorised, autonomy),
then compact rows, then details on demand. Preserve every number and every
ranking; move the secondary ones behind an expansion.

### P1 — No inspector for a stopped decision

The clause grid shows *which* clause failed. Nothing shows the four numbers a
person actually needs: what you said, what it costs, what you authorised, and
the gap. That is one small component and it is the most explanatory thing on
the page.

### P2 — Eight rooms, no entry point

The nav is calm and correct. But a reviewer landing after the opening has eight
choices and no suggestion. One "start here" affordance, not a redesign.

### P2 — Red is doing too many jobs

Kickers, the numeral watermark, the glow, the badges, failed clauses and the
CTA are all `--signal`. When everything is the accent, nothing is. Section
labels can drop to `--ink-4`; red should mean *state*.

### P3 — Mono is over-applied

Table bodies, notes and some prose are mono. The rule (mono = machine) is worth
holding to: numbers, ids, clauses, hashes — yes. Sentences — no.

---

## What must not change

1. The palette tokens. No new colours, in either direction.
2. Both families, and the mono/sans meaning rule.
3. The WebGL field. It is hand-written, it is a metaphor, and it is cheap.
4. The opening's structure and its `setTimeout` backstop.
5. The eight rooms and their order.
6. Every number and every ranking on the Arena page. This is a **visual** pass;
   the data does not move, and the fact that the frugal agent beats REMIT stays
   exactly where it is — it is the most interesting thing on the page.

---

## Acceptance

- [ ] No orange anywhere in `web/`
- [ ] The wordmark is read before the light
- [ ] Arena: winner, REMIT and the unsafe agent identifiable in one glance
- [ ] Arena rows do not overflow at 1440, 1024 or 390
- [ ] Every metric keeps a definition a reviewer can reach
- [ ] "Why did REMIT stop this?" answers in four numbers
- [ ] `prefers-reduced-motion` still lands everything on its end state
