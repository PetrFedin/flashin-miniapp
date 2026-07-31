from sqlalchemy import Index

from .models import BusinessEvent, MediaProcessingJob


def _index_names(table) -> set[str]:
    return {index.name for index in table.indexes if index.name}


def apply_queue_due_indexes() -> None:
    event_table = BusinessEvent.__table__
    media_table = MediaProcessingJob.__table__

    if "ix_business_events_due" not in _index_names(event_table):
        Index(
            "ix_business_events_due",
            event_table.c.status,
            event_table.c.next_attempt_at,
            event_table.c.id,
        )
    if "ix_media_processing_jobs_due" not in _index_names(media_table):
        Index(
            "ix_media_processing_jobs_due",
            media_table.c.status,
            media_table.c.next_attempt_at,
            media_table.c.id,
        )


apply_queue_due_indexes()
