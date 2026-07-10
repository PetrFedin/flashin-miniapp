def test_sla_job_imports():
    from backend.jobs.sla_jobs import mark_overdue_sla
    assert callable(mark_overdue_sla)
