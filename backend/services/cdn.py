import httpx


async def purge_cdn_url(url: str, purge_endpoint: str = "", token: str = "") -> dict:
    if not purge_endpoint:
        return {"ok": False, "skipped": True, "reason": "purge_endpoint_not_configured"}
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.post(purge_endpoint, json={"url": url}, headers=headers)
    return {"ok": res.status_code < 400, "status_code": res.status_code, "body": res.text[:500]}
