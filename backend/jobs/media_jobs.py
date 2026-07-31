from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import MediaAsset, MediaProcessingJob
from ..services.media_pipeline import generate_local_derivatives


def queue_missing_media_jobs(db: Session) -> int:
    asset_ids = {x[0] for x in db.query(MediaProcessingJob.media_asset_id).all()}
    count = 0
    for asset in db.query(MediaAsset).all():
        if asset.id not in asset_ids:
            db.add(MediaProcessingJob(media_asset_id=asset.id, status="pending"))
            count += 1
    db.commit()
    return count


def process_media_jobs(db: Session, limit: int = 20) -> int:
    rows = db.query(MediaProcessingJob).filter(MediaProcessingJob.status == "pending").limit(limit).all()
    count = 0
    for row in rows:
        asset = db.query(MediaAsset).filter(MediaAsset.id == row.media_asset_id).first()
        if not asset:
            row.status = "failed"
            row.last_error = "Media asset not found"
            continue
        try:
            generate_local_derivatives(db, asset)
            row.status = "processed"
            row.processed_at = utcnow_naive()
            count += 1
        except Exception as exc:
            row.attempts += 1
            row.last_error = str(exc)
            row.status = "failed" if row.attempts >= 5 else "pending"
    db.commit()
    return count
