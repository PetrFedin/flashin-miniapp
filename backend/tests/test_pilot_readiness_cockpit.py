from __future__ import annotations

from pathlib import Path

from backend.services.pilot_readiness import compose_pilot_readiness


def _diagnostics(*, overrides: dict[str, bool] | None = None) -> dict:
    checks = {
        "database": {"ok": True},
        "migrations": {"ok": True},
        "env": {"ok": True},
        "payments": {"ok": True},
        "moysklad": {"ok": True},
        "scheduler": {"ok": True},
        "notification_delivery": {"ok": True},
        "webhook_outbox": {"ok": True},
        "moysklad_sync": {"ok": True},
        "media": {"ok": True},
        "search": {"ok": True},
    }
    for name, ok in (overrides or {}).items():
        checks[name] = {"ok": ok}
    return {"ok": all(item["ok"] for item in checks.values()), "checks": checks}


def _runtime(**overrides) -> dict:
    snapshot = {
        "checkout_decision": "GO",
        "enforced": True,
        "runtime": {
            "status": "active",
            "accepted_orders": 3,
            "remaining_orders": 17,
            "allowlist_count": 2,
        },
        "database_integrity": {"healthy": True},
        "artifact_integrity": {"applicable": True, "healthy": True},
        "continuation": {"applicable": True, "ready": True, "next_sequence": 4},
        "money_attention": {"attention_required": False},
        "operational_safety": {"applicable": True, "healthy": True},
    }
    snapshot.update(overrides)
    return snapshot


def test_all_critical_signals_green_allows_next_order():
    result = compose_pilot_readiness(_diagnostics(), _runtime())

    assert result["decision"] == "GO"
    assert result["ready_for_next_order"] is True
    assert result["blocking_codes"] == []
    assert result["warning_codes"] == []
    assert result["runtime"]["remaining_orders"] == 17
    assert result["runtime"]["continuation_ready"] is True
    assert result["runtime"]["next_sequence"] == 4


def test_pending_previous_scenario_is_explicit_next_order_blocker():
    result = compose_pilot_readiness(
        _diagnostics(),
        _runtime(
            checkout_decision="NO-GO",
            continuation={"applicable": True, "ready": False, "next_sequence": 4},
        ),
    )

    assert result["decision"] == "NO-GO"
    assert result["ready_for_next_order"] is False
    assert "runtime_checkout_no_go" in result["blocking_codes"]
    assert "runtime_previous_scenario_pending" in result["blocking_codes"]


def test_critical_payment_diagnostic_blocks_next_order():
    result = compose_pilot_readiness(
        _diagnostics(overrides={"payments": False}),
        _runtime(),
    )

    assert result["decision"] == "NO-GO"
    assert result["ready_for_next_order"] is False
    assert "diagnostic_failed:payments" in result["blocking_codes"]


def test_migration_drift_blocks_next_order():
    result = compose_pilot_readiness(
        _diagnostics(overrides={"migrations": False}),
        _runtime(),
    )

    assert result["decision"] == "NO-GO"
    assert result["ready_for_next_order"] is False
    assert "diagnostic_failed:migrations" in result["blocking_codes"]


def test_search_degradation_is_visible_but_advisory():
    result = compose_pilot_readiness(
        _diagnostics(overrides={"search": False}),
        _runtime(),
    )

    assert result["decision"] == "GO"
    assert result["ready_for_next_order"] is True
    assert result["blocking_codes"] == []
    assert result["warning_codes"] == ["diagnostic_degraded:search"]


def test_runtime_no_go_blocks_even_when_diagnostics_are_green():
    result = compose_pilot_readiness(
        _diagnostics(),
        _runtime(checkout_decision="NO-GO"),
    )

    assert result["decision"] == "NO-GO"
    assert "runtime_checkout_no_go" in result["blocking_codes"]


def test_missing_snapshots_fail_closed_without_raw_error_details():
    result = compose_pilot_readiness(None, None)

    assert result["decision"] == "NO-GO"
    assert result["ready_for_next_order"] is False
    assert result["blocking_codes"] == [
        "diagnostics_unavailable",
        "runtime_status_unavailable",
    ]
    assert all(value is None for value in result["diagnostics"]["critical"].values())


def test_missing_runtime_integrity_evidence_fails_closed():
    runtime = _runtime(
        artifact_integrity={"applicable": False, "healthy": None},
        continuation={"applicable": True, "ready": None, "next_sequence": 4},
        operational_safety={"applicable": False, "healthy": None},
    )
    result = compose_pilot_readiness(_diagnostics(), runtime)

    assert result["decision"] == "NO-GO"
    assert "runtime_artifact_integrity_unavailable" in result["blocking_codes"]
    assert "runtime_operational_safety_unavailable" in result["blocking_codes"]
    assert "runtime_previous_scenario_pending" not in result["blocking_codes"]


def test_money_attention_is_an_explicit_blocker():
    result = compose_pilot_readiness(
        _diagnostics(),
        _runtime(money_attention={"attention_required": True, "secret": "never-expose-me"}),
    )

    assert result["decision"] == "NO-GO"
    assert "runtime_money_attention" in result["blocking_codes"]
    assert "never-expose-me" not in repr(result)


def test_cockpit_does_not_copy_raw_diagnostic_or_provider_details():
    diagnostics = _diagnostics()
    diagnostics["checks"]["payments"].update(
        {
            "provider": "yookassa",
            "secret_key": "super-secret-provider-key",
            "raw_payload": {"card": "4111111111111111"},
        }
    )
    runtime = _runtime(provider_payload={"token": "super-secret-runtime-token"})

    result = compose_pilot_readiness(diagnostics, runtime)
    rendered = repr(result)

    assert result["diagnostics"]["critical"]["payments"] is True
    assert "super-secret-provider-key" not in rendered
    assert "4111111111111111" not in rendered
    assert "super-secret-runtime-token" not in rendered
    assert "raw_payload" not in rendered


def test_admin_endpoint_is_read_only_protected_and_uncacheable():
    source = (
        Path(__file__).resolve().parents[1] / "api" / "ops.py"
    ).read_text(encoding="utf-8")

    assert '@router.get("/pilot-readiness")' in source
    assert 'require_permission(db, admin, "security.read")' in source
    assert 'response.headers["Cache-Control"] = "no-store, max-age=0"' in source
    assert 'response.headers["Pragma"] = "no-cache"' in source
    assert '@router.post("/pilot-readiness")' not in source
    assert 'readiness["request_id"] = getattr(request.state, "request_id", "")' in source