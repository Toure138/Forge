"""CSRF (synchronizer token, FR-11, AC-12): every unsafe method needs `X-CSRF-Token` equal to the
session's token; otherwise 403 `csrf_failed` and nothing is written."""

import uuid
from typing import Any

import psycopg
import pytest
from fastapi.routing import RouteContext
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.core import authz
from tests.api.test_route_protection import NOT_YET_PROTECTED
from tests.conftest import login
from tests.fakes.google import FakeGoogle
from tests.route_requests import UNSAFE_METHODS, api_routes, concrete_path, sample_body

CSRF_FAILED = {"code": "csrf_failed", "message": "Invalid or missing CSRF token."}


def mutating_routes() -> list[tuple[str, RouteContext]]:
    """(method, route) for every unsafe method of every protected route."""
    return [
        (method, route)
        for route in api_routes()
        if route.name not in authz.PUBLIC_ROUTES | NOT_YET_PROTECTED
        for method in sorted(route.methods & UNSAFE_METHODS)
    ]


MUTATING = mutating_routes()


def test_mutating_routes_are_found() -> None:
    assert ("POST", "auth_logout") in {(m, r.name) for m, r in MUTATING}


def global_rows(pg_app: psycopg.Connection) -> list[tuple[Any, ...]]:
    """Everything a request could write outside an organization (the organization tables are
    covered by the transaction: a refused request is rolled back)."""
    rows = []
    for table in ("users", "sessions", "login_requests"):
        rows += pg_app.execute(f"SELECT '{table}', * FROM {table} ORDER BY 2").fetchall()
    pg_app.commit()
    return rows


@pytest.mark.parametrize(("method", "route"), MUTATING, ids=[f"{m} {r.path}" for m, r in MUTATING])
@pytest.mark.parametrize("token", ["missing", "wrong"])
def test_every_mutating_route_requires_csrf(
    client: TestClient,
    fake_google: FakeGoogle,
    pg_app: psycopg.Connection,
    method: str,
    route: RouteContext,
    token: str,
) -> None:
    login(client, fake_google)
    client.get("/api/auth/session")  # last activity recorded before the snapshot
    before = global_rows(pg_app)
    headers = {"X-Organization-Id": str(uuid.uuid4())}
    if token == "wrong":
        headers["X-CSRF-Token"] = "not-the-session-token"

    response = client.request(
        method, concrete_path(route.path), headers=headers, json=sample_body(route)
    )

    assert response.status_code == 403
    assert response.json() == CSRF_FAILED
    assert global_rows(pg_app) == before


def test_wrong_csrf_token_changes_nothing(
    client: TestClient, fake_google: FakeGoogle, pg_app: psycopg.Connection
) -> None:
    login(client, fake_google)
    csrf_token = client.get("/api/auth/session").json()["csrf_token"]
    before = global_rows(pg_app)

    response = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_token + "x"})

    assert response.status_code == 403
    assert response.json() == CSRF_FAILED
    assert global_rows(pg_app) == before  # not revoked, activity not even recorded
    assert client.get("/api/auth/session").status_code == 200


def test_csrf_token_is_checked_against_the_session(
    client: TestClient, fake_google: FakeGoogle
) -> None:
    """The token of another session does not work: it is bound to the session (FR-11)."""
    login(client, fake_google)
    first_token = client.get("/api/auth/session").json()["csrf_token"]
    login(client, fake_google)  # new session, new token
    second_token = client.get("/api/auth/session").json()["csrf_token"]
    assert second_token != first_token

    response = client.post("/api/auth/logout", headers={"X-CSRF-Token": first_token})
    assert response.status_code == 403


class _Sample(BaseModel):
    email: str = Field(max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: str
    name: str = Field(min_length=1, max_length=100)
    note: str | None = None


def test_sample_body_builder_follows_the_schema() -> None:
    """The body builder used above produces valid bodies (here: pattern, required, optional)."""
    from tests import route_requests

    sample = route_requests._valid_sample(_Sample)
    assert sample == {"email": "user@example.com", "role": "x", "name": "x"}
