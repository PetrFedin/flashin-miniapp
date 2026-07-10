def test_campaign_job_imports():
    from backend.jobs.campaign_jobs import queue_due_campaigns
    assert callable(queue_due_campaigns)
