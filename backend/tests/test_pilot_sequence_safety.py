from backend.services.pilot_sequence_safety import (
    is_pilot_sequence_continuation_ready,
)


def _state(*results: str) -> dict:
    padded = [*results, *(["todo"] * (20 - len(results)))]
    return {
        "scenarios": [
            {"number": number, "result": result}
            for number, result in enumerate(padded, start=1)
        ]
    }


def test_first_pilot_order_does_not_require_prior_scenario_evidence():
    assert is_pilot_sequence_continuation_ready(_state(), accepted_orders=0) is True


def test_next_order_waits_for_every_prior_scenario_to_pass_in_order():
    assert is_pilot_sequence_continuation_ready(
        _state("pass", "pass"), accepted_orders=2
    ) is True
    assert is_pilot_sequence_continuation_ready(
        _state("pass", "running"), accepted_orders=2
    ) is False
    assert is_pilot_sequence_continuation_ready(
        _state("todo", "pass"), accepted_orders=2
    ) is False


def test_future_scenario_cannot_be_preapproved():
    assert is_pilot_sequence_continuation_ready(
        _state("pass", "pass"), accepted_orders=1
    ) is False


def test_scenario_numbers_must_match_exact_sequence():
    state = _state("pass")
    state["scenarios"][0]["number"] = 2
    assert is_pilot_sequence_continuation_ready(state, accepted_orders=1) is False


def test_invalid_counter_or_scenario_shape_fails_closed():
    assert is_pilot_sequence_continuation_ready(_state(), accepted_orders=-1) is False
    assert is_pilot_sequence_continuation_ready(_state(), accepted_orders=21) is False
    assert is_pilot_sequence_continuation_ready(_state(), accepted_orders=True) is False
    assert is_pilot_sequence_continuation_ready(
        {"scenarios": []}, accepted_orders=1
    ) is False
