# Contributing

## The rules that are not negotiable

This repository's whole argument is that its numbers are real. So:

1. **Never fabricate a metric.** Every number in a document must come from a
   command anyone can re-run.
2. **Never claim a test passed if it did not.**
3. **Never delete a failing security test because it looks bad.** Fix it, or
   record the failure in `FAILURES.md` and leave it failing.
4. **Never weaken ground truth to improve precision.**
5. **Never trust caller-provided identity.**
6. **Never let a model directly authorize money.**
7. **Every historical bug gets a regression test.** Write the test, confirm it
   FAILS against the old code, then fix it. A regression test that has never
   seen the bug is a guess.
8. **Every security boundary gets an executable invariant.**
9. **When uncertain, abstain.**

`tests/test_stated_numbers.py` enforces the first of these mechanically: it
reads the prose in this repository and asserts the numbers in it match what the
code counts.

## Running things

```bash
# server
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
python eval/attacks.py
python eval/matrix.py

# sdk
cd packages/sdk
npm install
npm run build
npm test
REMIT_TEST_URL=http://127.0.0.1:8099 node --test test/integration.test.js
```

## Before opening a PR

- `python -m pytest -q` is green, and you did not remove tests to get there.
- `python eval/attacks.py` **five times**, not once. A 1-in-3 concurrency flake
  in the money path has happened here before (`FAILURES #50`) and one green run
  would have hidden it.
- `npm run build && npm test && npm run typecheck` in `packages/sdk`.
- `npm pack` and read the file list. Nothing ships that should not.
- If you changed a number, `python eval/build_manifest.py`.

## Adding to FAILURES.md

Every real defect gets an entry: what happened, why nothing caught it, the fix,
and what it changed. Write the one about your own mistake in the same tone you
would write one about someone else's.

## Style

Comments explain *why*, especially where the code looks odd. If a line exists
because of a bug, say which bug.
