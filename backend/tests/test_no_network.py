"""AC-58: tests never reach the network (no Google, no LLM); only the loopback is allowed.

The guard is an autouse fixture of `tests/conftest.py` (`_block_outbound_network`).
"""

import socket

import psycopg
import pytest
from sqlalchemy.engine import make_url

from tests.conftest import NetworkBlockedError, database_url_from_env, libpq_url

# TEST-NET-1 (RFC 5737) and a public resolver: never contacted, the guard refuses first.
PUBLIC_ADDRESSES = [("192.0.2.1", 443), ("8.8.8.8", 53)]


@pytest.mark.parametrize("address", PUBLIC_ADDRESSES, ids=lambda a: a[0])
def test_outbound_network_is_blocked(address: tuple[str, int]) -> None:
    with pytest.raises(NetworkBlockedError, match="network access is blocked in tests"):
        socket.create_connection(address, timeout=1)

    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(NetworkBlockedError, match=address[0]),
    ):
        sock.connect_ex(address)


def test_outbound_ipv6_is_blocked() -> None:
    with (
        socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock,
        pytest.raises(NetworkBlockedError),
    ):
        sock.connect(("2001:db8::1", 443, 0, 0))


@pytest.mark.live
def test_live_tests_are_not_guarded() -> None:
    """`live` tests call the real LLM on purpose: the guard leaves `socket.connect` untouched."""
    assert socket.socket.connect.__qualname__ == "socket.connect"


def test_loopback_is_allowed() -> None:
    """The local Postgres stays reachable, through a Python socket and through libpq."""
    url = database_url_from_env("TEST_DATABASE_URL")
    parsed = make_url(url)
    assert parsed.host in {"127.0.0.1", "::1", "localhost"}
    with socket.create_connection((parsed.host, parsed.port or 5432), timeout=5):
        pass
    with psycopg.connect(libpq_url(url), connect_timeout=5) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)
