"""Which faults a caller may inject, and where.

REMIT's own demonstration surface takes a fault dictionary from the browser:
move the price after selection, blow up the shipping, delist the product,
inflate the quantity, expire the intent, revoke it. That is the right thing to
offer a reviewer — a boundary you cannot attack is a boundary you are being
asked to take on faith.

It is the wrong thing to offer against SHARED state.

Three of those faults write to the catalog every visitor reads:

    price     -> catalog.set_price(...)      persists, bumps catalog_version
    shipping  -> catalog.set_shipping(...)   persists
    delist    -> catalog.deactivate(...)     persists

So on the deployed instance, a reviewer pressing "raise the price 25%" raised
it for everyone who came after, permanently, and the next reviewer pressing it
raised it 25% again from there. The demo inflated its own catalog. Nothing
about that is an authorization bypass — `authorize()` still ran on every
request — but "any visitor can rewrite the merchant's prices" is not a thing a
control plane should ship, and a fault lab whose results depend on what the
previous visitor did is not a lab.

The split below is the whole file. IN_FLIGHT faults touch one journey and die
with it. SHARED faults write state other people read, and are legal only
against a throwaway instance — /api/probe, /api/compare, /api/attack, the
evaluation harness — never against the live one.
"""
from __future__ import annotations

# Faults whose entire blast radius is the journey that carries them.
IN_FLIGHT = frozenset({
    "qty",       # inflate the cart line after the human approved one
    "expire",    # push this journey's clock past the envelope's TTL
    "revoked",   # treat the mandate as revoked for this decision
    "payment",   # make the gateway fail or time out for this attempt
})

# Faults that write to state the next visitor reads.
SHARED = frozenset({
    "price",           # catalog.set_price
    "price_bump_pct",  # same, as a percentage
    "shipping",        # catalog.set_shipping
    "delist",          # catalog.deactivate
})

KNOWN = IN_FLIGHT | SHARED


def scrub(inject: dict | None, *, shared: bool) -> tuple[dict, list[str]]:
    """Return the faults this caller may actually inject, and the ones refused.

    `shared=True` means "this app is the deployed one, other people read it".

    Unknown keys are dropped in both cases rather than passed through. The
    journey ignores what it does not recognise, so passing them changes
    nothing — but silently accepting a key that does nothing is how a fault lab
    starts reporting that an attack was survived when it was never launched.
    """
    if not inject:
        return {}, []
    allowed = IN_FLIGHT if shared else KNOWN
    clean = {k: v for k, v in inject.items() if k in allowed}
    refused = sorted(k for k in inject if k not in allowed)
    return clean, refused


def refusal_note(refused: list[str]) -> str:
    """Said out loud in the response, because a fault that was quietly dropped
    looks exactly like a fault the system survived."""
    shared = [k for k in refused if k in SHARED]
    unknown = [k for k in refused if k not in KNOWN]
    parts = []
    if shared:
        parts.append(
            f"{', '.join(shared)} not applied: this fault writes to the catalog "
            f"every visitor reads, so it runs against a throwaway instance "
            f"(/api/probe) and never against the live one")
    if unknown:
        parts.append(f"{', '.join(unknown)} is not a fault this system models")
    return " · ".join(parts)
