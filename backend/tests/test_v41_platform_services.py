def test_event_dispatcher_imports():
    from backend.services.event_dispatcher import emit_event, process_pending_events
    assert callable(emit_event)
    assert callable(process_pending_events)

def test_media_pipeline_imports():
    from backend.services.media_pipeline import generate_local_derivatives
    assert callable(generate_local_derivatives)

def test_recommendation_engine_imports():
    from backend.services.recommendation_engine import rebuild_recommendations_v2
    assert callable(rebuild_recommendations_v2)
