"""Single error format `{code, message}` (plan, « Contrat API »), on a minimal FastAPI app."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import ApiError, add_error_handlers
from app.schemas.errors import ErrorBody


class Payload(BaseModel):
    name: str


def minimal_app() -> FastAPI:
    app = FastAPI()
    add_error_handlers(app)

    @app.get("/denied")
    async def denied() -> dict[str, str]:
        raise ApiError(403, "permission_denied", "Permission denied.")

    @app.post("/echo")
    async def echo(payload: Payload) -> Payload:
        return payload

    return app


def test_api_error_renders_code_and_generic_message() -> None:
    res = TestClient(minimal_app()).get("/denied")
    assert res.status_code == 403
    assert res.headers["content-type"] == "application/json"
    assert res.json() == {"code": "permission_denied", "message": "Permission denied."}
    assert ErrorBody.model_validate(res.json()) == ErrorBody(
        code="permission_denied", message="Permission denied."
    )


def test_api_error_exposes_its_fields() -> None:
    err = ApiError(404, "organization_not_found", "Organization not found.")
    assert (err.status_code, err.code, err.message) == (
        404,
        "organization_not_found",
        "Organization not found.",
    )
    assert "organization_not_found" in str(err)


def test_validation_errors_keep_fastapi_format() -> None:
    res = TestClient(minimal_app()).post("/echo", json={"nom": "x"})
    assert res.status_code == 422
    body = res.json()
    assert set(body) == {"detail"}
    assert body["detail"][0]["loc"] == ["body", "name"]
    assert body["detail"][0]["type"] == "missing"
