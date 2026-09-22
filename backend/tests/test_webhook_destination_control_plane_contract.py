from pathlib import Path
from types import SimpleNamespace

from backend.services.rbac import DEFAULT_PERMISSIONS, WEBHOOKS_CONFIGURE_PERMISSION, has_permission
from backend.services.webhook_security import redact_webhook_destination, redact_webhook_url


ROOT = Path(__file__).resolve().parents[1]
DESTINATIONS_SOURCE = (ROOT / "api" / "webhook_destinations.py").read_text(encoding="utf-8")
OUTBOX_SOURCE = (ROOT / "api" / "outbox.py").read_text(encoding="utf-8")


class _Query:
    def __init__(self, permissions):
        self.permissions = list(permissions)

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return [SimpleNamespace(permission=value) for value in self.permissions]


class _Db:
    def __init__(self, permissions=()):
        self.permissions = permissions

    def query(self, _model):
        return _Query(self.permissions)


def test_redaction_never_exposes_webhook_path_query_or_userinfo():
    raw = "https://user:password@hooks.example.com:8443/team/super-secret-token?key=query-secret"
    redacted = redact_webhook_url(raw)

    assert redacted == "https://hooks.example.com:8443/<redacted>"
    for secret in ("user", "password", "team", "super-secret-token", "key", "query-secret"):
        assert secret not in redacted


def test_redaction_fails_closed_and_hides_internal_destination_details():
    assert redact_webhook_url("https://[broken") == "<redacted>"
    assert redact_webhook_url("") == "<redacted>"
    assert redact_webhook_destination("internal://order-created/customer-42") == "internal://<redacted>"


def test_destination_reads_serialize_redacted_control_plane_view():
    list_block = DESTINATIONS_SOURCE.split('@router.get("", response_model=list[WebhookDestinationOut])', 1)[1].split(
        '@router.post("", response_model=WebhookDestinationOut)', 1
    )[0]

    assert 'require_permission(db, admin, "webhooks.read")' in list_block
    assert "_public_destination(row)" in list_block
    assert "return db.query(WebhookDestination)" not in list_block
    assert '"url": redact_webhook_url(row.url)' in DESTINATIONS_SOURCE


def test_destination_configuration_is_not_generic_webhook_operation_authority():
    create_block = DESTINATIONS_SOURCE.split('@router.post("", response_model=WebhookDestinationOut)', 1)[1].split(
        '@router.patch("/{destination_id}/active"', 1
    )[0]
    active_block = DESTINATIONS_SOURCE.split('@router.patch("/{destination_id}/active"', 1)[1]

    assert "WEBHOOKS_CONFIGURE_PERMISSION" in create_block
    assert "WEBHOOKS_CONFIGURE_PERMISSION" in active_block
    assert 'require_permission(db, admin, "webhooks.write")' not in create_block
    assert 'require_permission(db, admin, "webhooks.write")' not in active_block


def test_destination_audit_and_mutation_responses_do_not_expose_stored_url():
    create_block = DESTINATIONS_SOURCE.split('@router.post("", response_model=WebhookDestinationOut)', 1)[1].split(
        '@router.patch("/{destination_id}/active"', 1
    )[0]
    active_block = DESTINATIONS_SOURCE.split('@router.patch("/{destination_id}/active"', 1)[1]

    assert '"url": redact_webhook_url(url)' in create_block
    assert "return _public_destination(row)" in create_block
    assert "return _public_destination(row)" in active_block
    assert '"url": url' not in create_block


def test_outbox_read_surface_redacts_destination_and_raw_delivery_error():
    list_block = OUTBOX_SOURCE.split('@router.get("", response_model=list[WebhookOutboxOut])', 1)[1].split(
        '@router.post("/failed/requeue")', 1
    )[0]

    assert 'require_permission(db, admin, "webhooks.read")' in list_block
    assert "return [_public_outbox(row) for row in rows]" in list_block
    assert '"destination": redact_webhook_destination(row.destination)' in OUTBOX_SOURCE
    assert '"classification": delivery_classification_from_error(row.last_error)' in OUTBOX_SOURCE
    assert '"last_error": public_delivery_error(row.status, row.last_error)' in OUTBOX_SOURCE
    assert "Webhook delivery outcome requires review" not in OUTBOX_SOURCE
    assert "row.last_error[:500]" not in list_block
    assert ".limit(limit).all()" not in list_block


def test_outbox_retry_review_and_discard_remain_operational_webhook_write_actions():
    retry_block = OUTBOX_SOURCE.split('@router.post("/{row_id}/retry")', 1)[1].split(
        'def _validate_review_action', 1
    )[0]
    review_retry_block = OUTBOX_SOURCE.split('@router.post("/{row_id}/review/retry")', 1)[1].split(
        '@router.post("/{row_id}/review/mark-sent")', 1
    )[0]
    review_sent_block = OUTBOX_SOURCE.split('@router.post("/{row_id}/review/mark-sent")', 1)[1].split(
        '@router.post("/{row_id}/discard")', 1
    )[0]
    discard_block = OUTBOX_SOURCE.split('@router.post("/{row_id}/discard")', 1)[1]

    for block in (retry_block, review_retry_block, review_sent_block, discard_block):
        assert 'require_permission(db, admin, "webhooks.write")' in block
        assert "WEBHOOKS_CONFIGURE_PERMISSION" not in block

    assert '"had_error": bool(row.last_error)' in retry_block
    assert "row.last_error[:500]" not in retry_block
    assert '"event_id": row.id' in review_retry_block
    assert '"event_id": row.id' in review_sent_block
    assert "reason_code" in review_retry_block
    assert "reason_code" in review_sent_block


def test_default_operational_roles_do_not_inherit_destination_configuration():
    assert WEBHOOKS_CONFIGURE_PERMISSION == "webhooks.configure"
    for role in ("manager", "support", "warehouse"):
        assert WEBHOOKS_CONFIGURE_PERMISSION not in DEFAULT_PERMISSIONS[role]


def test_integration_admin_can_configure_without_outbox_mutation_authority():
    owner = SimpleNamespace(role="owner")
    assert has_permission(_Db(), owner, WEBHOOKS_CONFIGURE_PERMISSION) is True

    manager = SimpleNamespace(role="manager")
    assert has_permission(_Db(), manager, "webhooks.write") is True
    assert has_permission(_Db(), manager, WEBHOOKS_CONFIGURE_PERMISSION) is False

    integration_admin = SimpleNamespace(role="integration-admin")
    db = _Db(["webhooks.read", WEBHOOKS_CONFIGURE_PERMISSION])
    assert has_permission(db, integration_admin, WEBHOOKS_CONFIGURE_PERMISSION) is True
    assert has_permission(db, integration_admin, "webhooks.write") is False
