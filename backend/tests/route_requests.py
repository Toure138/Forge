"""Requests built from the application's routes, for the tests that sweep every route (CSRF, AC-12;
anonymous refusal, AC-30). They extend themselves to the routes of later phases.

`iter_route_contexts` (FastAPI 0.165, routers included lazily) yields the effective routes. Each
request gets random UUIDs in the path, a random `X-Organization-Id` and the smallest JSON body that
validates against the route's body model, so that the refusal under test never depends on the
order in which FastAPI checks dependencies and body (plan P11).
"""

import json
import re
import uuid
from typing import Any

from fastapi import FastAPI
from fastapi.routing import RouteContext, iter_route_contexts
from pydantic import TypeAdapter

from app.core.config import Settings
from app.main import create_app
from tests.conftest import fake_model

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def inventory_app() -> FastAPI:
    """The application as configured for tests, built without touching the database (only its
    routes are read: the lifespan never runs)."""
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+psycopg://unused@127.0.0.1:1/unused",
    )
    return create_app(fake_model(), settings=settings)


def api_routes() -> list[RouteContext]:
    return [r for r in iter_route_contexts(inventory_app().routes) if r.name is not None]


def concrete_path(path: str) -> str:
    """`/organization/members/{user_id}` → `/organization/members/<random uuid>`."""
    return re.sub(r"\{[^}]+\}", lambda _: str(uuid.uuid4()), path)


def sample_body(route: RouteContext) -> Any:
    """Smallest valid JSON body for `route` (None when it takes no body)."""
    params = route.dependant.body_params
    if not params:
        return None
    if len(params) == 1 and not getattr(params[0].field_info, "embed", False):
        return _valid_sample(params[0].field_info.annotation)
    return {p.alias: _valid_sample(p.field_info.annotation) for p in params}


def _valid_sample(annotation: Any) -> Any:
    adapter: TypeAdapter[Any] = TypeAdapter(annotation)
    schema = adapter.json_schema()
    value = _sample(schema, schema.get("$defs", {}))
    try:
        adapter.validate_json(json.dumps(value))
    except ValueError as exc:  # pragma: no cover - tells the next phase what to extend
        raise AssertionError(
            f"tests/route_requests.py cannot build a valid body for {annotation!r}: extend "
            f"_sample() ({exc})"
        ) from exc
    return value


_STRING_CANDIDATES = ("x", "user@example.com", "2026-11-24T12:00:00Z")


def _sample(schema: dict[str, Any], defs: dict[str, Any]) -> Any:
    if "$ref" in schema:
        return _sample(defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    for combinator in ("anyOf", "oneOf", "allOf"):
        if combinator in schema:
            options = [o for o in schema[combinator] if o.get("type") != "null"]
            return _sample(options[0], defs)
    kind = schema.get("type")
    if kind == "object":
        required = set(schema.get("required", ()))
        props = schema.get("properties", {})
        return {name: _sample(sub, defs) for name, sub in props.items() if name in required}
    if kind == "array":
        return [_sample(schema.get("items", {}), defs) for _ in range(schema.get("minItems", 0))]
    if kind == "string":
        return _string(schema)
    if kind == "integer":
        return max(1, int(schema.get("minimum", 1)))
    if kind == "number":
        return float(max(1, schema.get("minimum", 1)))
    if kind == "boolean":
        return True
    return None


def _string(schema: dict[str, Any]) -> str:
    if schema.get("format") == "uuid":
        return str(uuid.uuid4())
    if schema.get("format") == "date-time":
        return "2026-11-24T12:00:00Z"
    pattern = schema.get("pattern")
    if pattern is None:
        return "x" * max(1, schema.get("minLength", 1))
    for candidate in _STRING_CANDIDATES:
        if re.search(pattern, candidate):
            return candidate
    return "x"  # rejected by `_valid_sample`, with a message saying what to extend
