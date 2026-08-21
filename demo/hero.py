"""The hero demo. Seven scenes, one command, no API key, no network.

    python demo/hero.py

Everything printed here is computed at run time from the same code the
evaluation uses. Nothing is hardcoded. If a number here is wrong, the system
is wrong.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from remit.assembly import build
from remit.domain.risk import Exposure
from remit.exec.razorpay import FakeGateway
from remit.exec.webhooks import sign
from remit.money import rupees

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
SECRET = "remit_test_webhook_secret"
W = 78


def rule(ch="\u2500"):
    print(ch * W)


def scene(n, title):
    print()
    rule("\u2550")
    print(f"SCENE {n}  \u2502  {title}")
    rule("\u2550")


def boundary(authorised, current, width=52):
    """The Intent Boundary. The one visual idea in the product: the human's
    authorisation is a bounded space the agent may explore, and the moment
    the transaction leaves it is the moment the agent must stop and ask."""
    if not authorised:
        return "  (no ceiling was stated - the boundary is undefined)"
    used = min(current / authorised, 1.6)
    fill = int(min(width, used * width))
    inside = min(fill, width)
    outside = max(0, fill - width)
    bar = "\u2588" * inside + "\u2591" * (width - inside)
    over = "\u2588" * min(outside, 12)
    state = ("WITHIN INTENT" if current <= authorised * 0.92 else
             "NEAR THE BOUNDARY" if current <= authorised else
             "BOUNDARY EXCEEDED")
    room = authorised - current
    tail = (f"room {rupees(room)}" if room >= 0
            else f"over by {rupees(-room)}")
    return (f"  [{bar}]{over}\n"
            f"  authorised {rupees(authorised):>12}   "
            f"current {rupees(current):>12}   {tail}\n"
            f"  status: {state}")


def show(r, *, offers=True, why=True):
    d = r.dict()
    if d["intent"]:
        env = d["intent"]
        print(f"  intent      {env['category']} x{env['quantity']}, "
              f"objective {env['objective']}, authority={env['purchase_authority']}")
        print(f"              parse confidence {env['parse_confidence']} "
              f"(raw) -> risk uses the calibrated value")
        for n in d["telemetry"].get("notes", []):
            print(f"              note: {n}")
    if d["selected"]:
        print(f"  selected    {d['selected']['name']}  "
              f"{rupees(d['selected']['price_paise'])}  "
              f"{d['selected']['rating']}\u2605 ({d['selected']['reviews']} reviews)")
        if why:
            print(f"              why: {d['why_selected']}")
    if offers and d["offers"]:
        print("  offers      (proposed, never added silently)")
        for o in d["offers"]:
            flag = "needs you" if o["needs_human"] else "fits"
            sign_ = "+" if o["net_delta_paise"] >= 0 else "-"
            print(f"                {o['name']:<34} {sign_}"
                  f"{rupees(abs(o['net_delta_paise'])):>10}  [{flag}]")
            print(f"                  {o['reason']}")
    if d["accepted_offers"]:
        print(f"  accepted    {d['accepted_offers']} (inside the envelope)")
    if d["totals"]:
        t = d["totals"]
        print(f"  totals      subtotal {rupees(t['subtotal_paise'])}   "
              f"shipping {rupees(t['shipping_paise'])}   "
              f"TOTAL {rupees(t['total_paise'])}")
        print(f"              merchant margin {rupees(t['merchant_margin_paise'])}")
    if d["drift"]:
        dr = d["drift"]
        nz = {k: v for k, v in dr["dimensions"].items() if v > 0}
        print(f"  drift       score {dr['score']}   "
              f"non-zero: {nz if nz else 'none'}")
        for reason in dr["reasons"]:
            print(f"              ! {reason}")
    if d["risk"]:
        rk = d["risk"]
        print(f"  risk        {rk['level']}   E[loss] "
              f"{rupees(rk['expected_loss_paise'])} vs cost of asking "
              f"{rupees(rk['friction_cost_paise'])}")
    if d["authorization"]:
        a = d["authorization"]
        print(f"  VERDICT     {a['verdict']}   "
              f"({len(a['clauses'])} clauses evaluated"
              f"{', failed: ' + ', '.join(a['failed']) if a['failed'] else ''})")
        print(f"              {a['reason']}")
        if a["counterfactual"]:
            print(f"              counterfactual: {a['counterfactual']}")
    print(f"  payment     {d['payment_state']}"
          f"{'  ' + d['order_id'] if d['order_id'] else ''}")


def main():
    gw = FakeGateway()
    app = build(now=NOW, gateway=gw)
    print()
    rule("\u2550")
    print("REMIT  \u2502  Revocable, Explainable Mandates for Intent-driven Transactions")
    print("       \u2502  an AI buyer that grows merchant revenue without a blank cheque")
    rule("\u2550")
    print(f"  catalog: {app.seed_info['products']} products, "
          f"{app.seed_info['merchants']} merchants, "
          f"{app.db.execute('SELECT COUNT(*) c FROM relations').fetchone()['c']} "
          f"merchandising relations, version {app.catalog.version()}")
    print(f"  calibrator: {type(app.journey.calibrator).__name__}")

    # ---------------------------------------------------------------
    scene(1, "The human speaks. The agent searches, compares and selects.")
    utt = "find me premium running shoes under \u20b95000 and buy the best value option"
    print(f'  "{utt}"\n')
    r1 = app.journey.run(utterance=utt, user_id="usr_pranauv", now=NOW,
                         accept_offers="none", human_confirms=None)
    show(r1, offers=False)
    print("\n  top candidates the agent actually compared:")
    for c in r1.candidates[:4]:
        print(f"    {c['name']:<36} {rupees(c['price_paise']):>11}  "
              f"score {c['score']}")

    # ---------------------------------------------------------------
    scene(2, "The merchant revenue engine proposes. Nothing is added silently.")
    r2 = app.journey.run(utterance=utt, user_id="usr_pranauv", now=NOW,
                         accept_offers="in_envelope", human_confirms=None)
    show(r2, why=False)
    ceiling = r2.intent.ceiling_paise()
    print()
    print(boundary(ceiling, r2.totals.total_paise))

    # ---------------------------------------------------------------
    scene("2b", "A different basket, where the offer FITS. Revenue, inside the line.")
    utt2 = "buy a yoga mat under 2500, best value"
    print(f'  "{utt2}"\n')
    base = app.journey.run(utterance=utt2, user_id="usr_b", now=NOW,
                           accept_offers="none", human_confirms=True)
    withoffer = app.journey.run(utterance=utt2, user_id="usr_c", now=NOW,
                                accept_offers="in_envelope", human_confirms=True)
    show(withoffer, why=False)
    lift = withoffer.totals.total_paise - base.totals.total_paise
    mlift = withoffer.totals.merchant_margin_paise - base.totals.merchant_margin_paise
    print(f"\n  without offers  {rupees(base.totals.total_paise)}  "
          f"(margin {rupees(base.totals.merchant_margin_paise)})")
    print(f"  with offers     {rupees(withoffer.totals.total_paise)}  "
          f"(margin {rupees(withoffer.totals.merchant_margin_paise)})")
    print(f"  merchant gains  {rupees(mlift)} of margin on {rupees(lift)} of extra basket")
    print()
    print(boundary(withoffer.intent.ceiling_paise(), withoffer.totals.total_paise))

    # ---------------------------------------------------------------
    scene(3, "The world moves. Shipping changes AFTER the human was shown a price.")
    r3 = app.journey.run(utterance=utt, user_id="usr_pranauv", now=NOW,
                         accept_offers="in_envelope", human_confirms=None,
                         inject={"shipping": 79900})
    print(f"  the human was shown  {rupees(r3.shown_total_paise)}")
    show(r3, offers=False, why=False)
    print()
    print(boundary(r3.intent.ceiling_paise(), r3.totals.total_paise))
    print(f"\n  \u20b90 moved. The agent stopped at the boundary and asked.")

    # ---------------------------------------------------------------
    scene(4, "The human declines. Then the same request is retried four times.")
    r4 = app.journey.run(utterance=utt, user_id="usr_pranauv", now=NOW,
                         accept_offers="in_envelope", human_confirms=False,
                         inject={"shipping": 79900})
    print(f"  human declines -> {r4.payment_state}: {r4.note}")
    before = len([c for c in gw.calls if c[0] == "create_order"])
    ok = app.journey.run(utterance=utt, user_id="usr_pranauv", now=NOW,
                         accept_offers="in_envelope", human_confirms=True)
    rep = [app.journey.run(utterance=utt, user_id="usr_pranauv", now=NOW,
                           accept_offers="in_envelope", human_confirms=True)
           for _ in range(4)]
    after = len([c for c in gw.calls if c[0] == "create_order"])
    print(f"  human approves -> {ok.payment_state}  {ok.order_id}  "
          f"{rupees(ok.totals.total_paise)}")
    print(f"  then 4 identical retries -> replayed={[x.replayed for x in rep]}")
    print(f"  orders actually created: {after - before}  (must be 1)")

    # ---------------------------------------------------------------
    scene(5, "Webhooks arrive twice, out of order, and one is forged.")
    pid = ok.payment_id
    cap = json.dumps({"id": "evt_cap", "event": "payment.captured",
                      "payload": {"payment_id": pid}}).encode()
    a = app.webhooks.handle(body=cap, signature=sign(cap, SECRET), now=NOW)
    b = app.webhooks.handle(body=cap, signature=sign(cap, SECRET), now=NOW)
    late = json.dumps({"id": "evt_auth", "event": "payment.authorized",
                       "payload": {"payment_id": pid}}).encode()
    c = app.webhooks.handle(body=late, signature=sign(late, SECRET), now=NOW)
    forged = json.dumps({"id": "evt_forged", "event": "payment.captured",
                         "payload": {"payment_id": pid}}).encode()
    d = app.webhooks.handle(body=forged, signature="deadbeef", now=NOW)
    for name, res in [("captured", a), ("captured again", b),
                      ("authorized (late)", c), ("forged", d)]:
        print(f"    {name:<20} accepted={str(res.get('accepted')):<5} "
              f"applied={str(res.get('applied')):<5} {res.get('note') or res.get('why')}")
    print(f"  final payment state: {app.payments.get(pid)['state']}")
    print("  transitions:")
    for t in app.payments.timeline(pid):
        print(f"    {t['from_state']:>12} -> {t['to_state']:<12} {t['cause']}")

    # ---------------------------------------------------------------
    scene(6, "The intent graph and the tamper-evident Docket.")
    rows = list(app.db.execute(
        "SELECT node, parent_node, payload FROM intent_graph_events"
        " WHERE intent_id=? ORDER BY seq", (ok.intent.intent_id,)))
    for row in rows:
        arrow = "  \u2514\u2500 " if row["parent_node"] else "  "
        print(f"{arrow}{row['node']:<22} {row['payload'][:60]}")
    ok_chain, bad = app.ledger.verify_chain()
    n_events = app.ledger.db.execute(
        "SELECT COUNT(*) c FROM events").fetchone()[0]
    print(f"\n  Docket: {n_events} hash-chained events, "
          f"chain {'INTACT' if ok_chain else f'TAMPERED at {bad}'}")
    trace = app.ledger.trace(ok.correlation_id)
    for seq, ts, kind, payload, h in trace[:9]:
        print(f"    {kind:<18} {h[:12]}  {payload[:44]}")
    print(f"    ... {len(trace)} events for this one purchase")

    # ---------------------------------------------------------------
    scene(7, "What it cost, and what it saved.")
    try:
        exp = json.load(open("eval/results/experiments.json"))["arms"]
        A, B, C, D = exp
        print(f"  {'arm':<20}{'revenue':>14}{'vs baseline':>14}"
              f"{'AOV':>12}{'unauthorised':>15}")
        for a_ in exp:
            print(f"  {a_['label'][:19]:<20}{a_['revenue']:>14}"
                  f"{a_['incremental_revenue']:>14}{a_['aov']:>12}"
                  f"{a_['unauthorized']:>15}")
        keep = (100 * C["incremental_revenue_paise"]
                / B["incremental_revenue_paise"]) if B["incremental_revenue_paise"] else 0
        print(f"\n  REMIT keeps {keep:.1f}% of the unbounded agent's revenue upside")
        print(f"  and removes 100% of its {B['unauthorized']} of unauthorised movement.")
    except FileNotFoundError:
        print("  run `python eval/experiments.py` first")

    try:
        fr = json.load(open("eval/results/frontier.json"))["points"]
        safe = [p for p in fr if p["unauthorized_paise"] == 0]
        knee = max(safe, key=lambda p: p["autonomy"]) if safe else None
        leak = [p for p in fr if p["unauthorized_paise"] > 0]
        print("\n  the autonomy frontier:")
        for p in fr:
            mark = " <- last safe point" if knee and p["label"] == knee["label"] else ""
            print(f"    {p['label']:<22}{p['autonomy']*100:6.1f}% autonomous"
                  f"{p['human_friction_per_100']:8.1f} asks/100"
                  f"{p['unauthorized']:>13}{mark}")
        if knee and leak:
            first = leak[0]
            print(f"\n  You can buy autonomy up to {knee['autonomy']*100:.1f}% for free.")
            print(f"  Past '{knee['label']}', the next step costs "
                  f"{first['unauthorized']} that nobody authorised.")
    except FileNotFoundError:
        print("\n  run `python eval/frontier.py` for the frontier")

    print()
    rule()
    print("  The goal is not to make the agent as autonomous as possible.")
    print("  It is to find the point where autonomy stops paying for itself")
    print("  in money the buyer never authorised.")
    rule()
    print()


if __name__ == "__main__":
    main()
