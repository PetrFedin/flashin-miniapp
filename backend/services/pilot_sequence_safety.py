from __future__ import annotations

from collections.abc import Mapping
from typing import Any

PILOT_SCENARIO_COUNT = 20


def is_pilot_sequence_continuation_ready(
    pilot_state: Mapping[str, Any],
    *,
    accepted_orders: int,
) -> bool:
    """Return whether the signed pilot state permits admitting the next order.

    The controlled pilot is strictly sequential: after N accepted PostgreSQL
    pilot slots, scenarios 1..N must already be signed as pass before checkout
    N+1 may start. Future scenarios must not be pre-approved.

    This helper is deliberately pure and returns only a boolean so no scenario
    evidence, order identifier, provider detail, or operator data can leak into
    the customer-facing denial path.
    """

    if isinstance(accepted_orders, bool) or not isinstance(accepted_orders, int):
        return False
    if accepted_orders < 0 or accepted_orders > PILOT_SCENARIO_COUNT:
        return False
    if accepted_orders == 0:
        return True

    scenarios = pilot_state.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != PILOT_SCENARIO_COUNT:
        return False

    for sequence in range(1, accepted_orders + 1):
        record = scenarios[sequence - 1]
        if not isinstance(record, Mapping):
            return False
        if record.get("number") != sequence or record.get("result") != "pass":
            return False

    for record in scenarios[accepted_orders:]:
        if isinstance(record, Mapping) and record.get("result") == "pass":
            return False

    return True
