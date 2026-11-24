"""AC-29 (FR-25), deny by default: every exposed route declares a permission or is exempted.

A route is protected when one of its dependencies, at any depth, carries `__forge_permission__`
(set by `authz.declare_permission`). The only exemptions are `authz.PUBLIC_ROUTES`, by operation
name. Any other route, including a plain Starlette route (no dependency tree), is flagged.

FastAPI 0.165 includes routers lazily (`app.routes` holds `_IncludedRouter` objects):
`iter_route_contexts` yields the effective routes, whose `dependant` also contains the
dependencies declared at router level.
"""

import ast
import re
import uuid
from pathlib import Path
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

# Routes allowed to stay unprotected for a while. The agent routes of spec 000 were listed here
# until `require("agents:use")` protected them (spec 001, T036); it must stay empty.
NOT_YET_PROTECTED: frozenset[str] = frozenset()


def declares_permission(dependant: Dependant) -> bool:
    return any(
        authz.declared_permission(dep.call) is not None or declares_permission(dep)
        for dep in dependant.dependencies
    )


def declared_permissions(dependant: Dependant) -> set[str]:
    """Every permission declared in the dependency tree of a route."""
    found: set[str] = set()
    for dep in dependant.dependencies:
        permission = authz.declared_permission(dep.call)
        if permission is not None:
            found.add(permission)
        found |= declared_permissions(dep)
    return found


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
    """AC-29 (complete since T036): no route outside `PUBLIC_ROUTES` lacks a permission."""
    assert unprotected_routes(real_app()) == set()


def test_not_yet_protected_is_empty() -> None:
    """The temporary list of phase 2 is empty: every route, agents included, is protected."""
    assert frozenset() == NOT_YET_PROTECTED


def test_agent_routes_declare_agents_use() -> None:
    """FR-31: the three agent routes check `agents:use` (not merely some permission)."""
    agent_routes = {r.name: r for r in iter_route_contexts(real_app().routes)}
    for name in ("list_agents", "invoke_agent", "stream_agent"):
        assert declared_permissions(agent_routes[name].dependant) == {"agents:use"}, name


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


# --- AC-50 (FR-40): the audit log is append-only for the application ---

APP_DIR = Path(__file__).resolve().parents[2] / "app"
# The table's definition, and the single writer (`services.audit.record`).
AUDIT_TABLE_MODULES = frozenset({"db/tables.py", "services/audit.py"})
_RAW_AUDIT_SQL = re.compile(
    r"\b(UPDATE\s+audit_events|DELETE\s+FROM\s+audit_events|TRUNCATE\b[^;]*\baudit_events"
    r"|INSERT\s+INTO\s+audit_events)\b",
    re.IGNORECASE,
)


def _mentions_audit_table(node: ast.AST) -> bool:
    return any(
        (isinstance(n, ast.Attribute) and n.attr == "audit_events")
        or (isinstance(n, ast.Name) and n.id == "audit_events")
        for n in ast.walk(node)
    )


def _callee(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def audit_table_writers(root: Path) -> list[str]:
    """Code that could write the audit log other than `services.audit.record`: any mention of the
    table outside its definition and its writer, any UPDATE / DELETE of it anywhere, any raw SQL
    that writes it."""
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if module not in AUDIT_TABLE_MODULES and (
                (isinstance(node, ast.Attribute) and node.attr == "audit_events")
                or (isinstance(node, ast.Name) and node.id == "audit_events")
                or (isinstance(node, ast.alias) and node.name == "audit_events")
            ):
                found.append(f"{module}: uses audit_events")
            if (
                isinstance(node, ast.Call)
                and _callee(node) in {"update", "delete"}
                and any(_mentions_audit_table(arg) for arg in node.args)
            ):
                found.append(f"{module}: {_callee(node)}(audit_events)")
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _RAW_AUDIT_SQL.search(node.value)
            ):
                found.append(f"{module}: raw SQL on audit_events")
    return found


def test_audit_writer_finder_flags_synthetic_writers(tmp_path: Path) -> None:
    """The guard below, seen red on synthetic modules first."""
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "tables.py").write_text("audit_events = object()\n")
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "audit.py").write_text(
        "from app.db import tables\n"
        "def record(db): return insert(tables.audit_events)\n"
        "def erase(db): return delete(tables.audit_events)\n"
    )
    (tmp_path / "routes.py").write_text(
        "from app.db.tables import audit_events\n"
        "SQL = 'UPDATE audit_events SET outcome = 1'\n"
        "def fix(db): return update(audit_events)\n"
    )

    assert sorted(audit_table_writers(tmp_path)) == sorted(
        [
            "services/audit.py: delete(audit_events)",
            "routes.py: uses audit_events",
            "routes.py: uses audit_events",
            "routes.py: raw SQL on audit_events",
            "routes.py: update(audit_events)",
        ]
    )


def test_no_route_mutates_audit() -> None:
    """AC-50: the audit log is only read through the API (GET), and the application's only
    writer of the table is `services.audit.record` (an INSERT)."""
    audit_routes = [r for r in api_routes() if r.path.startswith("/api/organization/audit")]
    assert {(r.name, frozenset(r.methods)) for r in audit_routes} == {
        ("list_audit_events", frozenset({"GET"}))
    }
    assert audit_table_writers(APP_DIR) == []
