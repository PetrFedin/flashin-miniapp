import inspect
from pathlib import Path

from backend.jobs import scheduler_app


def test_scheduler_limits_threadpool_concurrency():
    source = inspect.getsource(scheduler_app.build_scheduler)

    assert '"max_workers": 4' in source
    assert '"coalesce": True' in source
    assert '"max_instances": 1' in source


def test_scheduler_and_workers_wait_for_backend_migrations():
    compose = (
        Path(__file__).resolve().parents[2] / "docker-compose.yml"
    ).read_text(encoding="utf-8")

    guarded_services = (
        "notification_worker",
        "ops_jobs",
        "outbox_jobs",
        "moysklad_sync",
        "campaign_jobs",
        "sla_jobs",
        "event_jobs",
        "media_jobs",
        "scheduler",
    )
    for service in guarded_services:
        start = compose.index(f"  {service}:\n")
        next_service = compose.find("\n  ", start + 3)
        section = compose[start:] if next_service == -1 else compose[start:next_service]
        assert "backend:" in section
        assert "condition: service_healthy" in section
