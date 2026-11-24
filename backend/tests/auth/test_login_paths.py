"""AC-7 (FR-6): outside the tests, the only way to get a session is the Google sign-in.

Static analysis of `app/`: a session is created by `create_session` (the only INSERT into the
`sessions` table), and `create_session` is called by the Google callback only. The application
contains no fake identity provider: Google is simulated in `tests/fakes/google.py` only.
"""

import ast
import re
from collections.abc import Iterator

from app.auth.google import GoogleOIDCProvider
from app.main import create_app
from tests.conftest import BACKEND_DIR, client_for, fake_model, settings_for_tests

APP_DIR = BACKEND_DIR / "app"
_RAW_INSERT = re.compile(r"\binsert\s+into\s+(?:public\.)?\"?sessions\b", re.IGNORECASE)


def app_sources() -> dict[str, str]:
    return {
        path.relative_to(BACKEND_DIR).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(APP_DIR.rglob("*.py"))
    }


class _SessionCreationFinder(ast.NodeVisitor):
    """Collects the functions that call `create_session` (under any alias) or insert into the
    `sessions` table (SQLAlchemy `insert(sessions)`, `sessions.insert()` or raw SQL)."""

    def __init__(self) -> None:
        self.stack: list[str] = []
        self.aliases = {"create_session"}
        self.sites: set[str] = set()

    def _here(self) -> str:
        return self.stack[-1] if self.stack else "<module>"

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "create_session" and alias.asname:
                self.aliases.add(alias.asname)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in self.aliases:
            self.sites.add(self._here())
        if name == "insert" and any(_is_sessions(arg) for arg in node.args):
            self.sites.add(self._here())
        if isinstance(func, ast.Attribute) and func.attr == "insert" and _is_sessions(func.value):
            self.sites.add(self._here())
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and _RAW_INSERT.search(node.value):
            self.sites.add(self._here())


def _is_sessions(node: ast.expr) -> bool:
    return (isinstance(node, ast.Name) and node.id == "sessions") or (
        isinstance(node, ast.Attribute) and node.attr == "sessions"
    )


def session_creation_sites(sources: dict[str, str]) -> set[tuple[str, str]]:
    """(module, function) of every place that creates a session."""
    sites: set[tuple[str, str]] = set()
    for module, source in sources.items():
        finder = _SessionCreationFinder()
        finder.visit(ast.parse(source, filename=module))
        sites |= {(module, function) for function in finder.sites}
    return sites


SYNTHETIC = """
from app.auth.sessions import create_session as open_session
from app.db import tables
from sqlalchemy import insert, text

async def backdoor(db, user_id, now):
    return await open_session(db, user_id, now=now)

async def raw_sql(db):
    await db.connection.execute(text("INSERT INTO sessions (token_hash) VALUES (:h)"))

async def core_insert(db):
    await db.connection.execute(insert(tables.sessions).values())

def method_insert():
    return tables.sessions.insert()

async def harmless(db):
    await db.connection.execute(insert(tables.users).values())
"""


def test_finder_flags_every_way_of_creating_a_session() -> None:
    """The guard, seen red on a synthetic module first."""
    assert session_creation_sites({"synthetic.py": SYNTHETIC}) == {
        ("synthetic.py", "backdoor"),
        ("synthetic.py", "raw_sql"),
        ("synthetic.py", "core_insert"),
        ("synthetic.py", "method_insert"),
    }


def test_session_creation_only_from_google_callback() -> None:
    assert session_creation_sites(app_sources()) == {
        ("app/api/routes/auth.py", "auth_callback"),  # the Google callback calls create_session
        ("app/auth/sessions.py", "create_session"),  # the one INSERT into `sessions`
    }


def _identifiers(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            yield node.name
        elif isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, ast.Attribute):
            yield node.attr
        elif isinstance(node, ast.alias):
            yield node.asname or node.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def identity_provider_classes(sources: dict[str, str]) -> set[str]:
    """Classes that implement the `IdentityProvider` protocol (both of its methods)."""
    found: set[str] = set()
    for source in sources.values():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ClassDef):
                methods = {
                    item.name
                    for item in node.body
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                }
                if {"authorization_url", "exchange"} <= methods:
                    found.add(node.name)
    return found


def test_no_fake_identity_provider_in_app_package() -> None:
    sources = app_sources()
    # The protocol itself declares both methods; the only implementation is Google's.
    assert identity_provider_classes(sources) == {"IdentityProvider", "GoogleOIDCProvider"}

    suspicious = re.compile(r"fake|mock|dummy|stub|bypass|impersonat", re.IGNORECASE)
    offenders = {
        (module, name)
        for module, source in sources.items()
        for name in _identifiers(ast.parse(source))
        if suspicious.search(name) or name == "tests" or name.startswith("tests.")
    }
    assert offenders == set()
    assert not list(APP_DIR.rglob("*fake*")) and not list(APP_DIR.rglob("*mock*"))

    # The running application signs in with Google, and nothing else.
    app = create_app(fake_model(), settings=settings_for_tests())
    with client_for(app):
        assert type(app.state.identity_provider) is GoogleOIDCProvider


def test_guard_flags_a_fake_provider() -> None:
    synthetic = (
        "class FakeProvider:\n"
        "    async def authorization_url(self, **kw): ...\n"
        "    async def exchange(self, **kw): ...\n"
    )
    assert identity_provider_classes({"x.py": synthetic}) == {"FakeProvider"}
    assert "FakeProvider" in set(_identifiers(ast.parse(synthetic)))


def test_scan_covers_the_app_package() -> None:
    """The guards above are only as good as the files they read."""
    sources = app_sources()
    assert {"app/main.py", "app/api/routes/auth.py", "app/auth/sessions.py"} <= set(sources)
