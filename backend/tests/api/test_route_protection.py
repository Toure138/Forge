"""AC-29 (FR-25), deny by default: every exposed route declares a permission or is exempted.

A route is protected when one of its dependencies, at any depth, carries `__forge_permission__`
(set by `authz.declare_permission`). The only exemptions are `authz.PUBLIC_ROUTES`, by operation
name. Any other route, including a plain Starlette route (no dependency tree), is flagged.

FastAPI 0.165 includes routers lazily (`app.routes` holds `_IncludedRouter` objects):
`iter_route_contexts` yields the effective routes, whose `dependant` also contains the
dependencies declared at router level.
"""

import uuid
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import RouteContext, iter_route_contexts
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.core import authz
from app.main import create_app
from tests.conftest import fake_model, settings_for_tests
from tests.route_requests import api_routes, concrete_path, sample_body

# Agent routes of spec 000, still open until `require("agents:use")` protects them.
# À vider en T036 : the test below then requires an empty set.
NOT_YET_PROTECTED = frozenset({"list_agents", "invoke", "stream"})


def declares_permission(dependant: Dependant) -> bool:
    return any(
        authz.declared_permission(dep.call) is not None or declares_permission(dep)
        for dep in dependant.dependencies
    )


def unprotected_routes(app: FastAPI) -> set[str]:
    """Names of the routes that neither declare a permission nor are exempted."""
    unprotected: set[str] = set()
    for route in iter_route_contexts(app.routes):
        if route.name in authz.PUBLIC_ROUTES:
            continue
        dependant: Dependant | None = getattr(route, "dependant", None)  # None: Starlette route
        if dependant is None or not declares_permission(dependant):
            unprotected.add(route.name or repr(route))
    return unprotected


def synthetic_app() -> FastAPI:
    async def checks_members_read() -> None:
        return None

    authz.declare_permission(checks_members_read, "members:read")

    async def outer(_: Annotated[None, Depends(checks_members_read)]) -> None:
        return None

    async def unmarked() -> None:
        return None

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health", name="health")
    async def health() -> None: ...

    @app.get("/naked", name="naked")
    async def naked() -> None: ...

    @app.get("/unmarked", name="unmarked_dep")
    async def unmarked_dep(_: Annotated[None, Depends(unmarked)]) -> None: ...

    @app.get("/guarded", name="guarded")
    async def guarded(_: Annotated[None, Depends(checks_members_read)]) -> None: ...

    @app.get("/nested", name="nested")
    async def nested(_: Annotated[None, Depends(outer)]) -> None: ...

    router = APIRouter(dependencies=[Depends(checks_members_read)])

    @router.get("/router-level", name="router_level")
    async def router_level() -> None: ...

    app.include_router(router, prefix="/api")

    async def raw(request: Request) -> PlainTextResponse:
        return PlainTextResponse("raw")

    app.add_route("/raw", raw, name="raw")
    return app


def real_app() -> FastAPI:
    return create_app(fake_model(), settings=settings_for_tests())


def test_checker_flags_route_without_permission() -> None:
    assert unprotected_routes(synthetic_app()) == {"naked", "unmarked_dep", "raw"}


def test_every_route_declares_permission_or_exemption() -> None:
    assert unprotected_routes(real_app()) - NOT_YET_PROTECTED == set()


def test_not_yet_protected_is_exactly_known_set() -> None:
    """A new forgotten route breaks this test; protecting an agent route requires removing it
    from `NOT_YET_PROTECTED` (emptied in T036)."""
    app = real_app()
    assert unprotected_routes(app) == NOT_YET_PROTECTED
    assert {route.name for route in iter_route_contexts(app.routes)} >= NOT_YET_PROTECTED


def test_exemptions_exist_as_routes() -> None:
    """Each exempted name is a real route: the exemption list cannot hold a stale name that a
    future route could silently take."""
    names = {route.name for route in iter_route_contexts(real_app().routes)}
    assert names >= authz.PUBLIC_ROUTES


PROTECTED = [
    (method, route)
    for route in api_routes()
    if route.name not in authz.PUBLIC_ROUTES | NOT_YET_PROTECTED
    for method in sorted(route.methods)
]


def test_protected_routes_are_found() -> None:
    assert {"auth_session", "auth_logout"} <= {route.name for _, route in PROTECTED}


@pytest.mark.parametrize(
    ("method", "route"), PROTECTED, ids=[f"{m} {r.path}" for m, r in PROTECTED]
)
def test_anonymous_request_to_protected_route_is_401(
    client: TestClient, method: str, route: RouteContext
) -> None:
    """AC-30 (FR-25): without a session, every non-exempted route answers 401, whatever the
    headers and the body."""
    headers = {"X-Organization-Id": str(uuid.uuid4()), "X-CSRF-Token": "anything"}
    response = client.request(
        method, concrete_path(route.path), headers=headers, json=sample_body(route)
    )
    assert response.status_code == 401
    assert response.json() == {"code": "not_authenticated", "message": "Authentication required."}
