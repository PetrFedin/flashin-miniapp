from fastapi import HTTPException
from sqlalchemy import CheckConstraint, Index, event, text

from .models import ConsentRecord, PrivacyRequest

PRIVACY_REQUEST_TYPES = frozenset({"export", "delete", "consent_withdrawal"})
PRIVACY_REQUEST_STATUSES = frozenset(
    {"requested", "processing", "processed", "superseded"}
)
CONSENT_TYPES = frozenset(
    {"marketing", "analytics", "personalization", "privacy", "terms"}
)
MAX_PRIVACY_RESULT_URL_LENGTH = 2048
MAX_CONSENT_SOURCE_LENGTH = 120


def _constraint_names(table) -> set[str]:
    return {constraint.name for constraint in table.constraints if constraint.name}


def _index_names(table) -> set[str]:
    return {index.name for index in table.indexes if index.name}


def _check(table, name: str, expression: str) -> None:
    if name not in _constraint_names(table):
        table.append_constraint(CheckConstraint(expression, name=name))


def _validate_privacy_request(_mapper, _connection, target: PrivacyRequest) -> None:
    target.request_type = str(target.request_type or "").strip().lower()
    target.status = str(target.status or "requested").strip().lower()
    target.result_url = str(target.result_url or "").strip()

    if target.request_type not in PRIVACY_REQUEST_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported privacy request type")
    if target.status not in PRIVACY_REQUEST_STATUSES:
        raise HTTPException(status_code=400, detail="Unsupported privacy request status")
    if len(target.result_url) > MAX_PRIVACY_RESULT_URL_LENGTH:
        raise HTTPException(status_code=400, detail="Privacy result URL is too long")

    terminal = target.status in {"processed", "superseded"}
    if terminal and target.processed_at is None:
        raise HTTPException(status_code=400, detail="Terminal privacy request requires processed_at")
    if not terminal and target.processed_at is not None:
        raise HTTPException(status_code=400, detail="Open privacy request cannot contain processed_at")
    if not terminal and target.result_url:
        raise HTTPException(status_code=400, detail="Open privacy request cannot contain a result URL")
    if target.request_type != "export" and target.result_url:
        raise HTTPException(
            status_code=400,
            detail="Only export privacy requests can contain a result URL",
        )


def _validate_consent(_mapper, _connection, target: ConsentRecord) -> None:
    target.consent_type = str(target.consent_type or "").strip().lower()
    target.source = str(target.source or "telegram_mini_app").strip().lower()
    if target.consent_type not in CONSENT_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported consent type")
    if not target.source:
        raise HTTPException(status_code=400, detail="Consent source is required")
    if len(target.source) > MAX_CONSENT_SOURCE_LENGTH:
        raise HTTPException(status_code=400, detail="Consent source is too long")


def apply_privacy_constraints() -> None:
    request = PrivacyRequest.__table__
    consent = ConsentRecord.__table__

    # `legacy_unknown` is migration-only quarantine. ORM validation never lets
    # application code create new records with that value.
    _check(
        request,
        "ck_privacy_requests_type_valid",
        "request_type IN ('consent_withdrawal', 'delete', 'export', 'legacy_unknown')",
    )
    _check(
        request,
        "ck_privacy_requests_status_valid",
        "status IN ('processed', 'processing', 'requested', 'superseded')",
    )
    _check(
        request,
        "ck_privacy_requests_type_normalized",
        "request_type = lower(trim(request_type))",
    )
    _check(
        request,
        "ck_privacy_requests_status_normalized",
        "status = lower(trim(status))",
    )
    _check(
        request,
        "ck_privacy_requests_result_url_normalized",
        "result_url = trim(result_url)",
    )
    _check(
        request,
        "ck_privacy_requests_result_url_size",
        f"length(result_url) <= {MAX_PRIVACY_RESULT_URL_LENGTH}",
    )
    _check(
        request,
        "ck_privacy_requests_state_coherent",
        "((status IN ('requested', 'processing') AND processed_at IS NULL AND result_url = '') "
        "OR (status IN ('processed', 'superseded') AND processed_at IS NOT NULL))",
    )
    _check(
        request,
        "ck_privacy_requests_result_type_coherent",
        "request_type = 'export' OR result_url = ''",
    )
    if "uq_privacy_requests_open_customer_type" not in _index_names(request):
        predicate = text("status IN ('requested', 'processing')")
        Index(
            "uq_privacy_requests_open_customer_type",
            request.c.customer_id,
            request.c.request_type,
            unique=True,
            postgresql_where=predicate,
            sqlite_where=predicate,
        )

    _check(
        consent,
        "ck_consent_records_type_valid",
        "consent_type IN ('analytics', 'legacy_unknown', 'marketing', 'personalization', 'privacy', 'terms')",
    )
    _check(
        consent,
        "ck_consent_records_type_normalized",
        "consent_type = lower(trim(consent_type))",
    )
    _check(
        consent,
        "ck_consent_records_source_normalized",
        "source = lower(trim(source))",
    )
    _check(
        consent,
        "ck_consent_records_source_size",
        f"length(source) BETWEEN 1 AND {MAX_CONSENT_SOURCE_LENGTH}",
    )


def _register_validation() -> None:
    for model, listener in (
        (PrivacyRequest, _validate_privacy_request),
        (ConsentRecord, _validate_consent),
    ):
        for event_name in ("before_insert", "before_update"):
            if not event.contains(model, event_name, listener):
                event.listen(model, event_name, listener)


apply_privacy_constraints()
_register_validation()
