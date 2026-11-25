"""API errors rendered as `ErrorBody {code, message}` (plan, « Contrat API »).

Raise `ApiError(status, code, message)` from a route, a dependency or a service; `create_app`
registers the handler with `add_error_handlers`. FastAPI's 422 validation errors keep their own
format.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.schemas.errors import ErrorBody


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(f"{status_code} {code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)  # registered for ApiError only
    body = ErrorBody(code=exc.code, message=exc.message)
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())


def add_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, api_error_handler)
