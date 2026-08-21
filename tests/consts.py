from datetime import datetime, timezone

# Fixed clock for every test. The policy engine never reads a clock; `now` is
# always an argument, which is what makes the counterfactual replay possible.
T0 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
