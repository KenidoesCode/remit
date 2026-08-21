"""Turning a model's self-reported confidence into a probability.

A raw confidence number from an LLM is not a probability -- it is a token
the model liked. Temperature scaling on a held-out split is the smallest
honest fix: one parameter, fitted on data you labelled, validated with a
reliability diagram and ECE.

Until you fit it on the real corpus (Day 7), `TEMPERATURE = 1.0` and the
calibrator is the identity function. Say so in the README rather than
shipping a fake number.
"""
from __future__ import annotations

import math


class TemperatureCalibrator:
    def __init__(self, temperature: float = 1.0):
        if temperature <= 0:
            raise ValueError("temperature must be > 0")
        self.t = temperature

    def __call__(self, raw: float) -> float:
        p = min(max(raw, 1e-6), 1 - 1e-6)
        logit = math.log(p / (1 - p))
        return 1.0 / (1.0 + math.exp(-logit / self.t))

    @classmethod
    def fit(cls, raws: list[float], correct: list[bool],
            lo: float = 0.25, hi: float = 6.0, steps: int = 240
            ) -> "TemperatureCalibrator":
        """Grid search on NLL. Small, transparent, defensible in a panel."""
        if not raws or len(raws) != len(correct):
            raise ValueError("mismatched inputs")
        best_t, best_nll = 1.0, float("inf")
        for i in range(steps):
            t = lo + (hi - lo) * i / (steps - 1)
            cal = cls(t)
            nll = 0.0
            for r, c in zip(raws, correct):
                p = min(max(cal(r), 1e-9), 1 - 1e-9)
                nll -= math.log(p) if c else math.log(1 - p)
            if nll < best_nll:
                best_t, best_nll = t, nll
        return cls(best_t)


def expected_calibration_error(ps: list[float], correct: list[bool],
                               bins: int = 10) -> float:
    n = len(ps)
    if n == 0:
        return 0.0
    ece = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(ps) if (lo < p <= hi) or (b == 0 and p == 0)]
        if not idx:
            continue
        conf = sum(ps[i] for i in idx) / len(idx)
        acc = sum(1 for i in idx if correct[i]) / len(idx)
        ece += (len(idx) / n) * abs(acc - conf)
    return ece


def risk_coverage(ps: list[float], correct: list[bool]) -> list[tuple[float, float]]:
    """(coverage, error-among-accepted) as the acceptance threshold sweeps.
    This curve, not a single accuracy number, is the honest report."""
    order = sorted(range(len(ps)), key=lambda i: -ps[i])
    out, wrong = [], 0
    for k, i in enumerate(order, start=1):
        if not correct[i]:
            wrong += 1
        out.append((k / len(ps), wrong / k))
    return out


class IsotonicCalibrator:
    """Isotonic regression via Pool-Adjacent-Violators.

    WHY THIS EXISTS, and it is not a preference:

    Temperature scaling has exactly one parameter, so it can only make a model
    uniformly more or less confident. Fitting it on this parser made ECE WORSE
    (train 0.168 -> 0.186, dev 0.145 -> 0.224), because the miscalibration runs
    in two directions at once:

        raw 0.4-0.6  says 0.54, is actually 0.35   <- over-confident
        raw 0.6-0.8  says 0.69, is actually 0.94   <- under-confident
        raw 0.8-1.0  says 0.99, is actually 0.94   <- over-confident

    No single temperature can pull one band down and push another up. Isotonic
    regression is non-parametric and only assumes the mapping is monotonic,
    which this one is once you look at the actual accuracies (0.35 < 0.94 ~ 0.94).

    Cost of the choice, stated: isotonic can overfit on small samples and
    produces a step function rather than a smooth curve. With a few hundred
    labelled cases that is an acceptable trade, and the dev-split ECE is the
    check on it.
    """

    def __init__(self, xs: list[float] | None = None, ys: list[float] | None = None):
        self.xs = xs or [0.0, 1.0]
        self.ys = ys or [0.0, 1.0]

    def __call__(self, raw: float) -> float:
        xs, ys = self.xs, self.ys
        if raw <= xs[0]:
            return ys[0]
        if raw >= xs[-1]:
            return ys[-1]
        for i in range(1, len(xs)):
            if raw <= xs[i]:
                x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
                if x1 == x0:
                    return y1
                t = (raw - x0) / (x1 - x0)
                return y0 + t * (y1 - y0)
        return ys[-1]

    @classmethod
    def fit(cls, raws: list[float], correct: list[bool]) -> "IsotonicCalibrator":
        pairs = sorted(zip(raws, [1.0 if c else 0.0 for c in correct]))
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        # PAVA: merge adjacent blocks that violate monotonicity.
        blocks = [[y, 1, x] for y, x in zip(ys, xs)]   # [mean, weight, x]
        i = 0
        while i < len(blocks) - 1:
            if blocks[i][0] > blocks[i + 1][0] + 1e-12:
                m0, w0, _ = blocks[i]
                m1, w1, x1 = blocks[i + 1]
                merged = [(m0 * w0 + m1 * w1) / (w0 + w1), w0 + w1, x1]
                blocks[i:i + 2] = [merged]
                if i:
                    i -= 1
            else:
                i += 1
        fx, fy = [], []
        for mean, _, x in blocks:
            fx.append(x)
            fy.append(min(1.0 - 1e-6, max(1e-6, mean)))
        if fx[0] > 0.0:
            fx.insert(0, 0.0)
            fy.insert(0, fy[0])
        if fx[-1] < 1.0:
            fx.append(1.0)
            fy.append(fy[-1])
        return cls(fx, fy)

    def to_dict(self) -> dict:
        return {"kind": "isotonic", "xs": self.xs, "ys": self.ys}

    @classmethod
    def from_dict(cls, d: dict) -> "IsotonicCalibrator":
        return cls(d["xs"], d["ys"])
