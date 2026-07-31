"""add audited scheduled job runs

Revision ID: 0032_scheduled_job_run_integrity
Revises: 0031_media_asset_integrity
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa

revision = "0032_scheduled_job_run_integrity"
down_revision = "0031_media_asset_integrity"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "scheduled_job_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_name", sa.String(length=120), nullable=False),
        sa.Column("run_token", sa.String(length=32), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.CheckConstraint(
            "status IN ('failed', 'running', 'skipped', 'succeeded')",
            name="ck_scheduled_job_runs_status_valid",
        ),
        sa.CheckConstraint(
            "trigger IN ('api', 'manual', 'scheduler', 'test', 'worker')",
            name="ck_scheduled_job_runs_trigger_valid",
        ),
        sa.CheckConstraint(
            "length(job_name) BETWEEN 1 AND 120",
            name="ck_scheduled_job_runs_job_name_size",
        ),
        sa.CheckConstraint(
            "job_name = lower(trim(job_name))",
            name="ck_scheduled_job_runs_job_name_normalized",
        ),
        sa.CheckConstraint(
            "length(run_token) = 32",
            name="ck_scheduled_job_runs_token_size",
        ),
        sa.CheckConstraint(
            "length(worker_id) BETWEEN 1 AND 255",
            name="ck_scheduled_job_runs_worker_size",
        ),
        sa.CheckConstraint(
            "length(result_json) <= 16384",
            name="ck_scheduled_job_runs_result_size",
        ),
        sa.CheckConstraint(
            "length(error) <= 2000",
            name="ck_scheduled_job_runs_error_size",
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms BETWEEN 0 AND 86400000",
            name="ck_scheduled_job_runs_duration_range",
        ),
        sa.CheckConstraint(
            "((status = 'running' AND finished_at IS NULL AND duration_ms IS NULL AND error = '') "
            "OR (status IN ('succeeded', 'skipped') AND finished_at IS NOT NULL "
            "AND duration_ms IS NOT NULL AND error = '') "
            "OR (status = 'failed' AND finished_at IS NOT NULL "
            "AND duration_ms IS NOT NULL AND length(trim(error)) > 0))",
            name="ck_scheduled_job_runs_state_coherent",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_token", name="uq_scheduled_job_runs_run_token"),
    )
    op.create_index(
        "ix_scheduled_job_runs_job_name",
        "scheduled_job_runs",
        ["job_name"],
    )
    op.create_index(
        "ix_scheduled_job_runs_run_token",
        "scheduled_job_runs",
        ["run_token"],
        unique=True,
    )
    op.create_index(
        "ix_scheduled_job_runs_started_at",
        "scheduled_job_runs",
        ["started_at"],
    )
    op.create_index(
        "ix_scheduled_job_runs_status",
        "scheduled_job_runs",
        ["status"],
    )
    op.create_index(
        "ix_scheduled_job_runs_job_started",
        "scheduled_job_runs",
        ["job_name", "started_at"],
    )


def downgrade():
    op.drop_index("ix_scheduled_job_runs_job_started", table_name="scheduled_job_runs")
    op.drop_index("ix_scheduled_job_runs_status", table_name="scheduled_job_runs")
    op.drop_index("ix_scheduled_job_runs_started_at", table_name="scheduled_job_runs")
    op.drop_index("ix_scheduled_job_runs_run_token", table_name="scheduled_job_runs")
    op.drop_index("ix_scheduled_job_runs_job_name", table_name="scheduled_job_runs")
    op.drop_table("scheduled_job_runs")
