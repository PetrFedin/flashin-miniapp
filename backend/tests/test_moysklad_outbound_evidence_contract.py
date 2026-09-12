from datetime import datetime

from backend.api.moysklad import _serialize_outbound_command
from backend.provider_models import ProviderCommand


def test_outbound_evidence_serializer_excludes_sensitive_command_data():
    command = ProviderCommand(
        id=17,
        provider="moysklad",
        command_type="moysklad.customer_order.create",
        idempotency_key="order:42:customer_order:v1",
        aggregate_type="order",
        aggregate_id="42",
        payload_json='{"secret":"must-not-leak"}',
        status="sent",
        attempts=2,
        external_id="provider-document-id",
        last_error="provider error detail must not leak",
        created_at=datetime(2026, 8, 11, 12, 0, 0),
        completed_at=datetime(2026, 8, 11, 12, 1, 0),
    )

    payload = _serialize_outbound_command(command)

    assert payload == {
        "id": 17,
        "provider": "moysklad",
        "command_type": "moysklad.customer_order.create",
        "aggregate_type": "order",
        "aggregate_id": "42",
        "status": "sent",
        "attempts": 2,
        "external_id": "provider-document-id",
        "created_at": "2026-08-11T12:00:00",
        "completed_at": "2026-08-11T12:01:00",
    }
    assert "payload_json" not in payload
    assert "idempotency_key" not in payload
    assert "last_error" not in payload
