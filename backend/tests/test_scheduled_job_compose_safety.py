import inspect
import re
from pathlib import Path

from backend.jobs import scheduler_app


def test_scheduler_limits_threadpool_concurrency():
    source = inspect.getsource(scheduler_app.build_scheduler)

    assert '"max_workers": 4' in source
    assert '"coalesce": True' in source
    assert '"max_instances": 1' in source


def _service_section(compose: str, service: str) -> str:
    start = compose.index(f"  {service}:\n")
    match = re.search(r"\n  [A-Za-z0-9_-]+:\n", compose[start + 1 :])
    if not match:
        return compose[start:]
    end = start + 1 + match.start()
    return compose[start:end]


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
        section = _service_section(compose, service)
        assert "backend:" in section
        assert "condition: service_healthy" in section
