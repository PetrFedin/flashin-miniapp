from backend.api.support import AdminSupportTicketOut


def test_admin_support_ticket_schema_exposes_accountable_owner():
    assert "assigned_admin_id" in AdminSupportTicketOut.model_fields
    assert "assigned_admin_id" not in AdminSupportTicketOut.__bases__[0].model_fields
