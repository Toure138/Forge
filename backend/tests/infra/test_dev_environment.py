"""Infrastructure tests (spec 001, AC-56 / AC-57): the real scripts against a real container.

Opt-in with RUN_INFRA=1 (`tests/conftest.py` skips them otherwise; about a minute, not part of
`check.ps1`, plan R8).

They NEVER touch the developer's `forge` project: every docker and script call runs with
COMPOSE_PROJECT_NAME=forge-smoke and POSTGRES_PORT=5434, and the `forge-smoke` project (container
and volume) is removed before and after each test, even when the test fails.
"""

import os
import socket
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from tests.conftest import BACKEND_DIR, REPO_ROOT, libpq_url
from tests.db.conftest import insert, set_context

pytestmark = pytest.mark.infra

SMOKE_PROJECT = "forge-smoke"
SMOKE_PORT = 5434
SMOKE_VOLUME = f"{SMOKE_PROJECT}_pgdata"
DEV_PROJECT = "forge"

DEV_SCRIPT = REPO_ROOT / "scripts" / "dev.ps1"
DB_RESET_SCRIPT = REPO_ROOT / "scripts" / "db-reset.ps1"

COMMAND_TIMEOUT_S = 300


class DevSettings(BaseSettings):
    """The developer's settings (environment, then repo-root `.env`), as the scripts see them."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str | None = None
    database_owner_url: str | None = None
    postgres_port: int = 5433


def on_smoke_port(url: str | None, var: str) -> str:
    """`url` (SQLAlchemy URL from the developer's settings) moved to the smoke port."""
    if not url:
        pytest.fail(f"{var} is not set: add it to the repo-root .env (see .env.example).")
    return make_url(url).set(port=SMOKE_PORT).render_as_string(hide_password=False)


@dataclass
class Result:
    returncode: int
    output: str  # stdout + stderr (never contains a password: no URL is printed)


@dataclass
class SmokeProject:
    """The `forge-smoke` compose project, driven exactly as a developer drives `forge`."""

    # Not in repr: pytest prints fixtures in tracebacks, and these carry passwords.
    env: dict[str, str] = field(repr=False)
    owner_url: str = field(repr=False)  # forge_owner on the smoke `forge` database (alembic)
    app_url: str = field(repr=False)  # forge_app on the smoke `forge` database (under RLS)

    def run(self, *command: str, cwd: Path = REPO_ROOT, stdin: str = "") -> Result:
        # Belt and braces: nothing in this module may run against the developer's project.
        assert self.env["COMPOSE_PROJECT_NAME"] == SMOKE_PROJECT
        assert self.env["POSTGRES_PORT"] == str(SMOKE_PORT)
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=self.env,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=COMMAND_TIMEOUT_S,
            check=False,
        )
        return Result(completed.returncode, completed.stdout + completed.stderr)

    def compose(self, *args: str) -> Result:
        return self.run("docker", "compose", *args)

    def powershell(self, script: Path, *args: str, stdin: str = "") -> Result:
        """Runs a repo script as `.\\scripts\\x.ps1` would, with `stdin` as the keyboard."""
        return self.run(
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *args,
            stdin=stdin,
        )

    def remove(self) -> None:
        """`docker compose down --volumes` on forge-smoke ONLY (container and volume)."""
        result = self.compose("down", "--volumes", "--remove-orphans", "--timeout", "5")
        assert result.returncode == 0, result.output

    def container_id(self) -> str:
        result = self.compose("ps", "--all", "--quiet", "db")
        assert result.returncode == 0, result.output
        return result.output.strip()

    def volume_exists(self) -> bool:
        result = self.run("docker", "volume", "ls", "--quiet", "--filter", f"name=^{SMOKE_VOLUME}$")
        assert result.returncode == 0, result.output
        return result.output.strip() == SMOKE_VOLUME

    def alembic_current(self) -> str:
        """Output of `uv run alembic current` (from backend/, DATABASE_OWNER_URL = smoke)."""
        result = self.run("uv", "run", "alembic", "current", cwd=BACKEND_DIR)
        assert result.returncode == 0, result.output
        return result.output

    def owner(self) -> psycopg.Connection:
        return psycopg.connect(libpq_url(self.owner_url), connect_timeout=5)

    def create_organization(self, name: str) -> uuid.UUID:
        """An organization row, written as forge_app under its own context (like the API)."""
        org = uuid.uuid4()
        with psycopg.connect(libpq_url(self.app_url), connect_timeout=5) as conn:
            set_context(conn, org=org)
            insert(
                conn, "organizations", {"id": org, "name": name, "created_at": datetime.now(UTC)}
            )
        return org

    def organization_exists(self, org: uuid.UUID) -> bool:
        with psycopg.connect(libpq_url(self.app_url), connect_timeout=5) as conn:
            set_context(conn, org=org)
            row = conn.execute("SELECT count(*) FROM organizations WHERE id = %s", (org,))
            count = row.fetchone()
        assert count is not None
        return bool(count[0])


def developer_project_state() -> str:
    """Containers and volumes of the developer's `forge` project (read-only docker calls)."""
    label = f"label=com.docker.compose.project={DEV_PROJECT}"
    containers = subprocess.run(
        ["docker", "ps", "--all", "--filter", label, "--format", "{{.ID}} {{.State}}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=COMMAND_TIMEOUT_S,
    ).stdout
    volumes = subprocess.run(
        ["docker", "volume", "ls", "--filter", label, "--format", "{{.Name}}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=COMMAND_TIMEOUT_S,
    ).stdout
    return containers + volumes


def port_is_free(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) != 0


@pytest.fixture
def smoke() -> Iterator[SmokeProject]:
    """A clean `forge-smoke` project (no container, no volume), removed again at the end."""
    settings = DevSettings()
    assert settings.postgres_port != SMOKE_PORT, "the smoke port must differ from the dev port"
    env = os.environ | {
        "COMPOSE_PROJECT_NAME": SMOKE_PROJECT,
        "POSTGRES_PORT": str(SMOKE_PORT),
        # Read by migrations/env.py (environment first): alembic migrates the smoke database.
        "DATABASE_OWNER_URL": on_smoke_port(settings.database_owner_url, "DATABASE_OWNER_URL"),
    }
    project = SmokeProject(
        env=env,
        owner_url=env["DATABASE_OWNER_URL"],
        app_url=on_smoke_port(settings.database_url, "DATABASE_URL"),
    )
    developer_before = developer_project_state()
    project.remove()  # leftovers of an interrupted run
    if not port_is_free(SMOKE_PORT):
        pytest.fail(f"Port {SMOKE_PORT} is already in use: free it to run the infra tests.")
    try:
        yield project
    finally:
        project.remove()
        assert not project.volume_exists(), f"{SMOKE_VOLUME} was not removed"
        assert developer_project_state() == developer_before, "the forge project changed"


def script_parameters(script: Path) -> set[str]:
    """Parameters declared by a PowerShell script's `param` block (parsed, never run)."""
    command = (
        "$ast = [System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script}', [ref]$null, [ref]$null); "
        "if ($ast.ParamBlock) { $ast.ParamBlock.Parameters.Name.VariablePath.UserPath }"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT_S,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return set(completed.stdout.split())


def alembic_head() -> str:
    head = ScriptDirectory.from_config(AlembicConfig(str(BACKEND_DIR / "alembic.ini")))
    revision = head.get_current_head()
    assert revision is not None
    return revision


def assert_migrated_to_head(smoke: SmokeProject) -> None:
    head = alembic_head()
    assert f"{head} (head)" in smoke.alembic_current()
    with smoke.owner() as conn:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row == (head,)


def start_database_like_dev_ps1(smoke: SmokeProject) -> None:
    """`dev.ps1 -DbOnly`: dev.ps1's prerequisites, database and migrations steps, no windows."""
    # Guard: without -DbOnly, dev.ps1 would open the backend and frontend windows.
    assert "DbOnly" in script_parameters(DEV_SCRIPT), "dev.ps1 has no -DbOnly switch"
    result = smoke.powershell(DEV_SCRIPT, "-DbOnly")
    assert result.returncode == 0, result.output


def test_db_up_creates_and_migrates_database(smoke: SmokeProject) -> None:
    """AC-56 (database part, R8): on a machine without the database, the launch command creates
    it and brings its structure up to date."""
    assert not smoke.volume_exists()

    start_database_like_dev_ps1(smoke)

    assert smoke.volume_exists()
    assert smoke.container_id()
    assert_migrated_to_head(smoke)
    with smoke.owner() as conn:
        roles = conn.execute(
            "SELECT rolname FROM pg_roles WHERE rolname LIKE 'forge\\_%' ORDER BY rolname"
        ).fetchall()
        databases = conn.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE 'forge%' ORDER BY datname"
        ).fetchall()
    assert [r[0] for r in roles] == ["forge_app", "forge_owner", "forge_self_reader"]
    assert [d[0] for d in databases] == ["forge", "forge_test"]


def test_data_persists_across_restart_and_reset_wipes_it(smoke: SmokeProject) -> None:
    """AC-57: an organization survives stopping and relaunching the environment; the reset
    command (`db-reset.ps1`) removes it and leaves a migrated database."""
    assert "Force" in script_parameters(DB_RESET_SCRIPT), "db-reset.ps1 has no -Force switch"
    start_database_like_dev_ps1(smoke)
    org = smoke.create_organization("Persisted Org")
    first_container = smoke.container_id()

    # The environment is stopped (container removed, volume kept) then launched again.
    stopped = smoke.compose("down")
    assert stopped.returncode == 0, stopped.output
    assert not smoke.container_id()
    assert smoke.volume_exists()
    start_database_like_dev_ps1(smoke)
    assert smoke.container_id() not in ("", first_container)
    assert smoke.organization_exists(org)

    # Without -Force, the script names what it will destroy and stops unless confirmed.
    refused = smoke.powershell(DB_RESET_SCRIPT, stdin="no\n")
    assert refused.returncode != 0, refused.output
    assert SMOKE_PROJECT in refused.output
    assert SMOKE_VOLUME in refused.output
    assert smoke.container_id()
    assert smoke.organization_exists(org)

    reset = smoke.powershell(DB_RESET_SCRIPT, "-Force")
    assert reset.returncode == 0, reset.output
    assert SMOKE_VOLUME in reset.output
    assert not smoke.organization_exists(org)
    assert_migrated_to_head(smoke)
