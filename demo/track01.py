"""Track 01, in one deterministic flow. Run: python demo/track01.py

No API key, no network. It builds a real REMIT instance, runs two journeys
through the real engine, and prints the authorization receipt for each -- the
same receipt the SDK, the CLI (`remit receipt show`) and the website all serve,
because they all read the same records.

The story it tells is the whole Track 01 requirement:

  1. A bounded authority. An agent proposes something inside it. AUTO, a
     Razorpay TEST-MODE order, and a receipt that explains why it was allowed.

  2. The same authority, an agent proposes something that is NOT what was asked
     for -- the catalog's best "laptop" is a laptop STAND. REMIT steps up, no
     money moves, and the receipt names the clause. That is the failure handled
     gracefully: the deterministic boundary refusing an action the model was
     confident about, and leaving a verifiable record of the refusal.

Nothing here is scripted to a verdict. Both journeys run through the real policy
engine; if the policy changed, this script's output would change with it.
"""
from datetime import datetime, timezone

from remit.assembly import build
from remit.exec.razorpay import FakeGateway
from remit.receipt import build_receipt, render_text

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
USER = "usr_demo"


def _run_and_print(app, utterance, *, human_confirms):
    r = app.journey.run(utterance=utterance, user_id=USER, now=NOW,
                        human_confirms=human_confirms)
    receipt = build_receipt(app, r.correlation_id, USER)
    print(render_text(receipt))
    print()
    return r, receipt


def main() -> int:
    app = build(now=NOW, gateway=FakeGateway())

    print("=" * 40)
    print("TRACK 01 — one authority, two proposals")
    print("=" * 40)
    print()

    print(">>> Scene 1: the agent proposes something inside the authority.")
    print(">>> A human has authorised it, so money may move.")
    print()
    _r1, rec1 = _run_and_print(app, "buy a yoga mat under 2000",
                               human_confirms=True)

    print(">>> Scene 2: same shopper, a harder sentence. The best match this")
    print(">>> catalog has for 'laptop' is a laptop STAND. No human is present")
    print(">>> to approve, so watch what does NOT happen.")
    print()
    _r2, rec2 = _run_and_print(app, "buy a laptop under 50000",
                               human_confirms=None)

    # The two lines that are the whole argument, asserted rather than narrated.
    print("=" * 40)
    print("WHAT JUST HAPPENED")
    print("=" * 40)
    print(f"  Scene 1  {rec1['decision']['verdict']:8}  "
          f"money moved: {rec1['execution']['money_moved']}")
    print(f"  Scene 2  {rec2['decision']['verdict']:8}  "
          f"money moved: {rec2['execution']['money_moved']}"
          f"   (stopped by {', '.join(rec2['decision']['failed_clauses']) or 'policy'})")
    print()

    ok = (rec1["execution"]["money_moved"] is True
          and rec2["execution"]["money_moved"] is False)
    if ok:
        print("  A bounded action executed. An unauthorised one did not, and")
        print("  left a receipt that says so. Verify either with:")
        print(f"      remit receipt verify <correlation-id>")
        return 0
    print("  UNEXPECTED: the demo's own invariant did not hold. Do not ship this.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
