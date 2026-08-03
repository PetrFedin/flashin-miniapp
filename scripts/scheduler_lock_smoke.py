#!/usr/bin/env python3
"""Prove scheduler jobs are single-owner across PostgreSQL connections."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.jobs.scheduler_lock import (
    advisory_lock_key,
    run_with_scheduler_lock,
)


def main() -> int:
    token = uuid.uuid4().hex[:20]
    same_job = f"scheduler-smoke:{token}"
    other_job = f"scheduler-smoke-other:{token}"
    failing_job = f"scheduler-smoke-failure:{token}"
    trace: list[str] = []

    same_key = advisory_lock_key(same_job)
    assert same_key == advisory_lock_key(same_job)
    assert same_key != advisory_lock_key(other_job)
    assert -(2**63) <= same_key < 2**63

    def outer_callback() -> dict:
        duplicate = run_with_scheduler_lock(
            same_job,
            lambda: trace.append("duplicate-must-not-run"),
        )
        assert duplicate == {
            "status": "skipped",
            "reason": "lock_busy",
            "job": same_job,
        }

        independent = run_with_scheduler_lock(
            other_job,
            lambda: trace.append("independent"),
        )
        assert independent["status"] == "executed"
        assert independent["job"] == other_job
        assert independent["result"] is None

        trace.append("outer")
        return {
            "duplicate": duplicate["status"],
            "independent": independent["status"],
        }

    outer = run_with_scheduler_lock(same_job, outer_callback)
    assert outer["status"] == "executed"
    assert outer["job"] == same_job
    assert outer["result"] == {
        "duplicate": "skipped",
        "independent": "executed",
    }
    assert trace == ["independent", "outer"]

    reacquired = run_with_scheduler_lock(
        same_job,
        lambda: trace.append("reacquired"),
    )
    assert reacquired["status"] == "executed"
    assert trace == ["independent", "outer", "reacquired"]

    def fail_after_lock() -> None:
        trace.append("failed")
        raise RuntimeError("scheduler smoke failure")

    try:
        run_with_scheduler_lock(failing_job, fail_after_lock)
    except RuntimeError as exc:
        assert str(exc) == "scheduler smoke failure"
    else:
        raise AssertionError("Scheduler callback exception was swallowed")

    after_failure = run_with_scheduler_lock(
        failing_job,
        lambda: trace.append("after-failure"),
    )
    assert after_failure["status"] == "executed"
    assert trace[-2:] == ["failed", "after-failure"]

    for invalid_name in ("", " ", "bad job", "x" * 121):
        try:
            run_with_scheduler_lock(invalid_name, lambda: None)
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"Invalid scheduler job name was accepted: {invalid_name!r}"
            )

    try:
        run_with_scheduler_lock("scheduler-smoke-invalid-callback", None)  # type: ignore[arg-type]
    except TypeError:
        pass
    else:
        raise AssertionError("Non-callable scheduler callback was accepted")

    print(
        json.dumps(
            {
                "status": "ok",
                "same_job": same_job,
                "duplicate_execution": "skipped",
                "independent_execution": "executed",
                "reacquired_after_release": True,
                "reacquired_after_exception": True,
                "trace": trace,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
