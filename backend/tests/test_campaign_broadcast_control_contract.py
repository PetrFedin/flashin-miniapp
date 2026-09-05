from pathlib import Path
from types import SimpleNamespace

from backend.services.rbac import (
    CAMPAIGNS_READ_PERMISSION,
    CAMPAIGNS_SEND_PERMISSION,
    CAMPAIGNS_WRITE_PERMISSION,
    DEFAULT_PERMISSIONS,
    has_permission,
)


ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = (ROOT / "api" / "campaigns.py").read_text(encoding="utf-8")
SERVICE_SOURCE = (ROOT / "services" / "campaigns.py").read_text(encoding="utf-8")
JOB_SOURCE = (ROOT / "jobs" / "campaign_jobs.py").read_text(encoding="utf-8")


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


def test_campaign_routes_no_longer_inherit_support_write():
    assert 'require_permission(db, admin, "support.write")' not in API_SOURCE
    assert "CAMPAIGNS_WRITE_PERMISSION" in API_SOURCE
    assert "CAMPAIGNS_READ_PERMISSION" in API_SOURCE
    assert API_SOURCE.count("CAMPAIGNS_SEND_PERMISSION") >= 3


def test_broadcast_requires_latest_explicit_marketing_consent():
    assert 'func.max(ConsentRecord.id).label("consent_id")' in SERVICE_SOURCE
    assert 'ConsentRecord.consent_type == "marketing"' in SERVICE_SOURCE
    assert "ConsentRecord.granted.is_(True)" in SERVICE_SOURCE
    assert "consented_customers.c.customer_id == Customer.id" in SERVICE_SOURCE
    assert "CrmProfile.segment == segment" in SERVICE_SOURCE


def test_campaign_enqueue_is_serialized_and_replay_safe():
    assert ".with_for_update()" in SERVICE_SOURCE
    assert ".populate_existing()" in SERVICE_SOURCE
    assert 'if locked.status == "queued":' in SERVICE_SOURCE
    assert "CampaignQueueResult(queued=0, changed=False)" in SERVICE_SOURCE
    assert "locked.status = \"queued\"" in SERVICE_SOURCE
    assert "if result.changed:" in JOB_SOURCE


def test_send_actions_are_locked_and_audited_without_message_content():
    queue_block = API_SOURCE.split('@router.post("/{campaign_id}/queue")', 1)[1].split(
        '@router.get("", response_model=list[MarketingCampaignOut])', 1
    )[0]
    schedule_block = API_SOURCE.split('@router.post("/{campaign_id}/schedule")', 1)[1]

    assert "CAMPAIGNS_SEND_PERMISSION" in queue_block
    assert '"campaign.queue"' in queue_block
    assert '"message"' not in queue_block
    assert "CAMPAIGNS_SEND_PERMISSION" in schedule_block
    assert ".with_for_update()" in schedule_block
    assert 'campaign.status == "queued"' in schedule_block
    assert '"campaign.schedule"' in schedule_block
    assert '"message"' not in schedule_block


def test_campaign_input_is_bounded_to_telegram_safe_message_size():
    assert "len(name) > 255" in API_SOURCE
    assert "len(segment) > 120" in API_SOURCE
    assert "len(message) > 4096" in API_SOURCE


def test_default_roles_separate_draft_management_from_broadcast():
    assert CAMPAIGNS_READ_PERMISSION == "campaigns.read"
    assert CAMPAIGNS_WRITE_PERMISSION == "campaigns.write"
    assert CAMPAIGNS_SEND_PERMISSION == "campaigns.send"

    assert CAMPAIGNS_READ_PERMISSION in DEFAULT_PERMISSIONS["manager"]
    assert CAMPAIGNS_WRITE_PERMISSION in DEFAULT_PERMISSIONS["manager"]
    assert CAMPAIGNS_SEND_PERMISSION not in DEFAULT_PERMISSIONS["manager"]

    for role in ("support", "warehouse"):
        assert CAMPAIGNS_READ_PERMISSION not in DEFAULT_PERMISSIONS[role]
        assert CAMPAIGNS_WRITE_PERMISSION not in DEFAULT_PERMISSIONS[role]
        assert CAMPAIGNS_SEND_PERMISSION not in DEFAULT_PERMISSIONS[role]


def test_marketer_can_broadcast_without_generic_support_authority():
    owner = SimpleNamespace(role="owner")
    assert has_permission(_Db(), owner, CAMPAIGNS_SEND_PERMISSION) is True

    marketer = SimpleNamespace(role="marketer")
    db = _Db(
        [
            CAMPAIGNS_READ_PERMISSION,
            CAMPAIGNS_WRITE_PERMISSION,
            CAMPAIGNS_SEND_PERMISSION,
        ]
    )
    assert has_permission(db, marketer, CAMPAIGNS_SEND_PERMISSION) is True
    assert has_permission(db, marketer, "support.write") is False
