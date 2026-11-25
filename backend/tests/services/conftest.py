"""Service tests run on `forge_test` through `RequestDb`, like the API. They reuse the SQL-level
world of `tests/db/conftest.py` (Acme and Globex, one row of every organization table each)."""

from tests.db.conftest import two_orgs

__all__ = ["two_orgs"]
