"""Structured logs and per-stage timing, on the path that already exists.

Observability was the lowest row on the readiness scorecard (3/10) and the
honest reason was that REMIT had correlation ids threaded through everything
and no way to see them. `print` in two places, a `latency_ms` on the journey
result that measured the whole thing, and nothing that could answer "which
stage was slow" or "what happened to correlation id cor_abc".

Two things here, and deliberately not a third:

    · a JSON log line per decision, keyed on the correlation id
    · a stage timer, so p50/p95/p99 exist per stage rather than per journey

There is no metrics endpoint, no tracing exporter and no log shipper. Those
need infrastructure this deployment does not have, and a /metrics route that
serves numbers nobody scrapes is decoration. The gap is named in
docs/OBSERVABILITY.md.

WHAT IS NOT LOGGED
------------------
The utterance is in the audit ledger, where it is evidence and access-
controlled. It is NOT in the log line, because logs go to stdout, stdout goes
to a hosting provider, and a shopping sentence is the user's, not the
operator's. Amounts, verdicts, clause ids and timings are operational and are.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import deque
from contextlib import contextmanager

# Off by default. A library that writes to stdout the moment it is imported is
# a library that corrupts somebody's JSON output.
ENABLED = os.environ.get("REMIT_LOG", "").lower() in ("1", "json", "true")

# Bounded: this is a demo instance, not a time-series database. Enough samples
# for a stable p99 on a stage, few enough that memory is not a story.
_WINDOW = 2048
_STAGES: dict[str, deque[float]] = {}


def log(event: str, correlation_id: str | None = None, **fields) -> None:
    """One line, JSON, stdout. Never raises -- an observability layer that can
    break the request it is observing is worse than no observability layer."""
    if not ENABLED:
        return
    try:
        line = {"ts": time.time(), "event": event}
        if correlation_id:
            line["cid"] = correlation_id
        line.update(fields)
        sys.stdout.write(json.dumps(line, default=str) + "\n")
    except Exception:
        pass


@contextmanager
def stage(name: str):
    """Time one stage. Recorded whether or not logging is on, because the
    percentiles are read by the API and shown on the page."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        ms = (time.perf_counter() - t0) * 1000
        _STAGES.setdefault(name, deque(maxlen=_WINDOW)).append(ms)


def record(name: str, ms: float) -> None:
    _STAGES.setdefault(name, deque(maxlen=_WINDOW)).append(float(ms))


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((len(xs) - 1) * p))))
    return round(xs[k], 3)


def percentiles() -> dict[str, dict]:
    """p50, p95, p99 and n, per stage.

    p99 is reported with its sample count beside it on purpose: a p99 over
    eleven samples is the second-slowest request wearing a statistic's clothes,
    and a number without its n invites exactly that reading.
    """
    out = {}
    for name, xs in _STAGES.items():
        vals = list(xs)
        out[name] = {"n": len(vals), "p50": _pct(vals, .50),
                     "p95": _pct(vals, .95), "p99": _pct(vals, .99),
                     "max": round(max(vals), 3) if vals else 0.0}
    return out


def reset() -> None:
    _STAGES.clear()
