import uuid
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .middleware.request_context import REQUEST_ID_HEADER


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        return request_id

    request_id = uuid.uuid4().hex
    request.state.request_id = request_id
    return request_id


def _headers(
    request_id: str,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    headers = dict(extra or {})
    headers[REQUEST_ID_HEADER] = request_id
    headers.setdefault("Cache-Control", "no-store")
    return headers


def _public_validation_errors(exc: RequestValidationError) -> list[dict[str, object]]:
    """Remove submitted values and validator context from public 422 responses."""
    public_fields = ("loc", "msg", "type")
    return [
        {field: error[field] for field in public_fields if field in error}
        for error in exc.errors()
    ]


async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    request_id = _request_id(request)
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder(
            {
                "detail": exc.detail,
                "request_id": request_id,
            }
        ),
        headers=_headers(request_id, exc.headers),
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    request_id = _request_id(request)
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder(
            {
                "detail": _public_validation_errors(exc),
                "request_id": request_id,
            }
        ),
        headers=_headers(request_id),
    )


async def unhandled_exception_handler(
    request: Request,
    _exc: Exception,
) -> JSONResponse:
    request_id = _request_id(request)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "request_id": request_id,
        },
        headers=_headers(request_id),
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
