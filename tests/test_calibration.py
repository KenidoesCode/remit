from remit.risk.calibration import (
    TemperatureCalibrator, expected_calibration_error, risk_coverage,
)


def test_identity_at_t1():
    c = TemperatureCalibrator(1.0)
    assert abs(c(0.7) - 0.7) < 1e-9


def test_fit_softens_an_overconfident_model():
    """A model that says 0.95 and is right 60% of the time must be pulled
    down, or the expected-loss arithmetic is built on a lie."""
    raws = [0.95] * 100
    correct = [True] * 60 + [False] * 40
    cal = TemperatureCalibrator.fit(raws, correct)
    assert cal(0.95) < 0.95


def test_ece_is_zero_for_a_perfect_calibrator():
    ps = [0.0] * 50 + [1.0] * 50
    correct = [False] * 50 + [True] * 50
    assert expected_calibration_error(ps, correct) < 1e-9


def test_risk_coverage_is_monotone_in_coverage():
    ps = [0.9, 0.8, 0.7, 0.6]
    correct = [True, True, False, False]
    rc = risk_coverage(ps, correct)
    assert rc[0][1] == 0.0
    assert rc[-1][1] == 0.5
