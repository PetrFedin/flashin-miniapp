from fastapi import APIRouter

# v1 router facade. Current modules remain available under /api.
# New public clients should progressively migrate to /api/v1.
router = APIRouter(prefix="/v1", tags=["v1"])


@router.get("/version")
def api_version():
    return {"version": "v1", "status": "active"}
