"""Users are identified by Google's stable `sub`, profile refreshed at each sign-in (FR-3, AC-3)."""

from sqlalchemy import func, select

from app.auth.provider import VerifiedIdentity
from app.auth.users import normalize_email, upsert_user
from app.db import tables
from tests.conftest import RequestDbFactory
from tests.fakes.clock import FakeClock


def identity(**overrides: object) -> VerifiedIdentity:
    values: dict[str, object] = {
        "sub": "100000000000000000042",
        "email": "Zoe.Martin@Example.com",
        "email_verified": True,
        "name": "Zoé Martin",
        "picture": "https://lh3.googleusercontent.com/a/zoe",
    }
    return VerifiedIdentity(**(values | overrides))  # type: ignore[arg-type]


def test_normalize_email_ignores_case_only() -> None:
    """R6: case-insensitive comparison only; Gmail aliases are not normalized."""
    assert normalize_email("Zoe.Martin@Example.COM") == "zoe.martin@example.com"
    assert normalize_email("zoe+tag@gmail.com") == "zoe+tag@gmail.com"


async def test_upsert_user_by_google_sub_updates_profile(
    request_db_factory: RequestDbFactory,
) -> None:
    clock = FakeClock()
    async with request_db_factory() as db:
        first = await upsert_user(db, identity(), now=clock.now())
    assert first.email == "Zoe.Martin@Example.com"
    assert first.email_normalized == "zoe.martin@example.com"

    clock.advance(days=3)
    changed = identity(
        email="zoe@new-domain.example", name="Zoé M.", picture=None
    )  # same Google account
    async with request_db_factory() as db:
        second = await upsert_user(db, changed, now=clock.now())

    assert second.id == first.id
    assert (second.email, second.email_normalized, second.name, second.picture_url) == (
        "zoe@new-domain.example",
        "zoe@new-domain.example",
        "Zoé M.",
        None,
    )
    async with request_db_factory() as db:
        rows = await db.connection.execute(select(tables.users))
        (row,) = rows.mappings().all()
        assert row["created_at"] == FakeClock().now()  # creation date kept
        assert row["last_login_at"] == clock.now()
        count = await db.connection.execute(select(func.count()).select_from(tables.users))
        assert count.scalar_one() == 1


async def test_same_email_with_another_google_account_is_another_user(
    request_db_factory: RequestDbFactory,
) -> None:
    """FR-3: the identity is the `sub`, not the email (an address can change hands)."""
    clock = FakeClock()
    async with request_db_factory() as db:
        first = await upsert_user(db, identity(), now=clock.now())
        other = await upsert_user(db, identity(sub="100000000000000000099"), now=clock.now())
    assert other.id != first.id
