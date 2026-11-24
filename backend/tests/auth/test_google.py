"""`GoogleOIDCProvider` against the fake Google (`tests/fakes/google.py`), no network."""

import base64
import hashlib
from collections.abc import AsyncIterator
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest

from app.auth.google import GoogleOIDCProvider
from app.auth.provider import (
    IdentityError,
    IdentityProviderUnavailable,
    VerifiedIdentity,
    code_challenge_s256,
    new_code_verifier,
)
from app.core.config import Settings
from tests.fakes import google as fake_google_module
from tests.fakes.clock import FakeClock
from tests.fakes.google import (
    CLIENT_ID,
    CLIENT_SECRET,
    GMAIL_ACCOUNT,
    WORKSPACE_ACCOUNT,
    FakeGoogle,
    GoogleAccount,
)

REDIRECT_URI = "https://testserver/api/auth/callback"


def google_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "google_client_id": CLIENT_ID,
        "google_client_secret": CLIENT_SECRET,
        "oidc_redirect_uri": REDIRECT_URI,
    }
    return Settings(_env_file=None, **(values | overrides))  # type: ignore[arg-type]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake(clock: FakeClock) -> FakeGoogle:
    return FakeGoogle(clock)


@pytest.fixture
async def provider(fake: FakeGoogle, clock: FakeClock) -> AsyncIterator[GoogleOIDCProvider]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as http:
        yield GoogleOIDCProvider(google_settings(), http, clock)


def test_code_challenge_is_s256_of_verifier() -> None:
    # RFC 7636, appendix B.
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert code_challenge_s256(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    fresh = new_code_verifier()
    assert 43 <= len(fresh) <= 128  # RFC 7636 §4.1
    digest = hashlib.sha256(fresh.encode("ascii")).digest()
    assert code_challenge_s256(fresh) == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def test_authorization_url_requests_basic_identity_with_pkce(
    provider: GoogleOIDCProvider,
) -> None:
    """AC-4 (FR-4): basic identity only, no Drive, no offline access; PKCE S256, state, nonce."""
    url = await provider.authorization_url(
        state="the-state", nonce="the-nonce", code_challenge="the-challenge"
    )
    parts = urlsplit(url)
    assert (
        f"{parts.scheme}://{parts.netloc}{parts.path}" == fake_google_module.AUTHORIZATION_ENDPOINT
    )
    params = {k: v[0] for k, v in parse_qs(parts.query, strict_parsing=True).items()}
    assert set(params["scope"].split(" ")) == {"openid", "email", "profile"}
    assert "drive" not in url.lower()
    assert "access_type" not in params
    assert params["response_type"] == "code"
    assert params["client_id"] == CLIENT_ID
    assert params["redirect_uri"] == REDIRECT_URI
    assert params["prompt"] == "select_account"
    assert params["code_challenge_method"] == "S256"
    assert params["code_challenge"] == "the-challenge"
    assert params["state"] == "the-state"
    assert params["nonce"] == "the-nonce"
    assert "client_secret" not in params
    assert CLIENT_SECRET not in url


async def test_discovery_document_is_cached(provider: GoogleOIDCProvider, fake: FakeGoogle) -> None:
    for _ in range(3):
        await provider.authorization_url(state="s", nonce="n", code_challenge="c")
    assert fake.count(fake_google_module.DISCOVERY_URL) == 1


@pytest.mark.parametrize(
    "missing", ["google_client_id", "google_client_secret", "oidc_redirect_uri"]
)
async def test_authorization_url_unavailable_without_configuration(
    fake: FakeGoogle, clock: FakeClock, missing: str
) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as http:
        provider = GoogleOIDCProvider(google_settings(**{missing: None}), http, clock)
        with pytest.raises(IdentityProviderUnavailable, match="not configured"):
            await provider.authorization_url(state="s", nonce="n", code_challenge="c")
    assert fake.requests == []


async def sign_in(
    provider: GoogleOIDCProvider,
    fake: FakeGoogle,
    account: GoogleAccount = GMAIL_ACCOUNT,
    *,
    nonce: str = "the-nonce",
) -> VerifiedIdentity:
    """The whole flow, minus the browser: authorization URL, consent, code exchange."""
    verifier = new_code_verifier()
    url = await provider.authorization_url(
        state="s", nonce=nonce, code_challenge=code_challenge_s256(verifier)
    )
    code = fake.authorize(url, account)
    return await provider.exchange(code=code, code_verifier=verifier, nonce=nonce)


async def test_exchange_returns_verified_identity(
    provider: GoogleOIDCProvider, fake: FakeGoogle
) -> None:
    identity = await sign_in(provider, fake, GMAIL_ACCOUNT)
    assert identity == VerifiedIdentity(
        sub=GMAIL_ACCOUNT.sub,
        email=GMAIL_ACCOUNT.email,
        email_verified=True,
        name=GMAIL_ACCOUNT.name,
        picture=GMAIL_ACCOUNT.picture,
    )
    # The code was exchanged server to server, with the PKCE verifier and the client secret.
    token_request = next(
        r for r in fake.requests if str(r.url) == fake_google_module.TOKEN_ENDPOINT
    )
    form = parse_qs(token_request.content.decode())
    assert form["grant_type"] == ["authorization_code"]
    assert form["redirect_uri"] == [REDIRECT_URI]
    assert form["client_secret"] == [CLIENT_SECRET]
    assert form["code_verifier"] == [fake.received_code_verifiers[-1]]


DEFAULT_SKEW = 60  # `Settings.oidc_clock_skew_seconds` default


def _expired(clock: FakeClock) -> dict[str, object]:
    """Expired beyond the clock-skew margin (inside it, the token is still accepted)."""
    past = int(clock.now().timestamp()) - DEFAULT_SKEW - 1
    return {"iat": past - 3600, "exp": past}


@pytest.mark.parametrize(
    "tamper",
    [
        "bad_signature",
        "bad_audience",
        "bad_issuer",
        "expired",
        "nonce_mismatch",
        "azp_mismatch",
        "issued_in_future",
        "missing_sub",
    ],
)
async def test_id_token_rejected(
    provider: GoogleOIDCProvider, fake: FakeGoogle, clock: FakeClock, tamper: str
) -> None:
    """AC-5 (unit side): any defect of the ID token refuses the sign-in."""
    future = int(clock.now().timestamp()) + 600
    overrides: dict[str, dict[str, object]] = {
        "bad_audience": {"aud": "another-client.apps.googleusercontent.com"},
        "bad_issuer": {"iss": "https://accounts.evil.example"},
        "expired": _expired(clock),
        "nonce_mismatch": {"nonce": "a-nonce-from-another-login"},
        "azp_mismatch": {"azp": "another-client.apps.googleusercontent.com"},
        "issued_in_future": {"iat": future, "exp": future + 3600},
        "missing_sub": {"sub": None},
    }
    fake.sign_with_foreign_key = tamper == "bad_signature"
    fake.claim_overrides = overrides.get(tamper, {})
    with pytest.raises(IdentityError) as caught:
        await sign_in(provider, fake)
    assert caught.value.reason == "invalid_id_token"


@pytest.mark.parametrize(
    ("provider_lag", "accepted"),
    # The token lives 1 h; past `exp`, it stays accepted for the clock-skew margin (60 s).
    [(timedelta(minutes=59), True), (timedelta(hours=1, seconds=DEFAULT_SKEW), False)],
)
async def test_time_claims_use_the_injected_clock(provider_lag: timedelta, accepted: bool) -> None:
    """`iat` / `exp` are checked against the injected clock (plan T14), not the system's: Google's
    clock is set far from the real date, and the provider's clock runs ahead of it by
    `provider_lag`."""
    google_clock = FakeClock(datetime(2031, 6, 1, 8, 0, tzinfo=UTC))
    provider_clock = FakeClock(google_clock.now() + provider_lag)
    fake = FakeGoogle(google_clock)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as http:
        provider = GoogleOIDCProvider(google_settings(), http, provider_clock)
        if accepted:
            assert (await sign_in(provider, fake)).sub == GMAIL_ACCOUNT.sub
        else:
            with pytest.raises(IdentityError) as caught:
                await sign_in(provider, fake)
            assert caught.value.reason == "invalid_id_token"


@pytest.mark.parametrize(
    ("iat_offset", "exp_offset", "accepted"),
    [
        # Offsets in seconds from the provider's (injected) clock; margin = 60 s, inclusive for
        # `iat` (`iat <= now + skew`), exclusive for `exp` (`now < exp + skew`).
        pytest.param(DEFAULT_SKEW, DEFAULT_SKEW + 3600, True, id="iat_in_future_within_skew-ok"),
        pytest.param(
            DEFAULT_SKEW + 1, DEFAULT_SKEW + 3601, False, id="iat_in_future_beyond_skew-refused"
        ),
        pytest.param(-3600, 1 - DEFAULT_SKEW, True, id="exp_just_passed_within_skew-ok"),
        pytest.param(-3600, -DEFAULT_SKEW, False, id="exp_passed_beyond_skew-refused"),
    ],
)
async def test_time_claims_tolerate_clock_skew(
    provider: GoogleOIDCProvider,
    fake: FakeGoogle,
    clock: FakeClock,
    iat_offset: int,
    exp_offset: int,
    accepted: bool,
) -> None:
    """M1 (review): Google's clock and ours may differ by a few seconds. A small margin
    (`OIDC_CLOCK_SKEW_SECONDS`, 60 s by default) is tolerated on `iat` and `exp`, always against
    the injected clock."""
    now = int(clock.now().timestamp())
    fake.claim_overrides = {"iat": now + iat_offset, "exp": now + exp_offset}
    if accepted:
        assert (await sign_in(provider, fake)).sub == GMAIL_ACCOUNT.sub
    else:
        with pytest.raises(IdentityError) as caught:
            await sign_in(provider, fake)
        assert caught.value.reason == "invalid_id_token"


async def test_clock_skew_setting_is_applied(fake: FakeGoogle, clock: FakeClock) -> None:
    """The margin comes from the settings: with 0 s, a token issued 1 s ahead is refused."""
    now = int(clock.now().timestamp())
    fake.claim_overrides = {"iat": now + 1, "exp": now + 3601}
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as http:
        strict = GoogleOIDCProvider(google_settings(oidc_clock_skew_seconds=0), http, clock)
        with pytest.raises(IdentityError) as caught:
            await sign_in(strict, fake)
        assert caught.value.reason == "invalid_id_token"
        tolerant = GoogleOIDCProvider(google_settings(oidc_clock_skew_seconds=1), http, clock)
        assert (await sign_in(tolerant, fake)).sub == GMAIL_ACCOUNT.sub


async def test_unverified_email_is_rejected(provider: GoogleOIDCProvider, fake: FakeGoogle) -> None:
    """FR-2 / AC-2 (unit side)."""
    account = replace(GMAIL_ACCOUNT, email_verified=False)
    with pytest.raises(IdentityError) as caught:
        await sign_in(provider, fake, account)
    assert caught.value.reason == "email_not_verified"


async def test_jwks_reloaded_on_unknown_kid(provider: GoogleOIDCProvider, fake: FakeGoogle) -> None:
    await sign_in(provider, fake)
    await sign_in(provider, fake)
    assert fake.count(fake_google_module.JWKS_URI) == 1  # cached while the kid is known

    fake.rotate_key()
    identity = await sign_in(provider, fake)
    assert identity.sub == GMAIL_ACCOUNT.sub
    assert fake.count(fake_google_module.JWKS_URI) == 2  # reloaded once for the new kid


async def test_unknown_kid_after_reload_is_rejected(
    provider: GoogleOIDCProvider, fake: FakeGoogle
) -> None:
    def sign_with_unpublished_kid(claims: dict[str, object]) -> str:
        return jwt.encode(claims, fake.private_key, algorithm="RS256", headers={"kid": "nope"})

    fake.sign = sign_with_unpublished_kid  # type: ignore[method-assign]
    with pytest.raises(IdentityError) as caught:
        await sign_in(provider, fake)
    assert caught.value.reason == "invalid_id_token"


async def test_hd_claim_is_ignored(provider: GoogleOIDCProvider, fake: FakeGoogle) -> None:
    """FR-1: a Workspace account (`hd` claim) and a Gmail account are treated alike."""
    workspace = await sign_in(provider, fake, WORKSPACE_ACCOUNT)
    gmail = await sign_in(provider, fake, GMAIL_ACCOUNT)
    assert (workspace.sub, workspace.email) == (WORKSPACE_ACCOUNT.sub, WORKSPACE_ACCOUNT.email)
    assert (gmail.sub, gmail.email) == (GMAIL_ACCOUNT.sub, GMAIL_ACCOUNT.email)
    assert {f.name for f in fields(VerifiedIdentity)} == {
        "sub",
        "email",
        "email_verified",
        "name",
        "picture",
    }


async def test_access_token_is_not_kept(provider: GoogleOIDCProvider, fake: FakeGoogle) -> None:
    """FR-5: the identity carries no token, and the provider keeps none."""
    identity = await sign_in(provider, fake)
    tokens = fake.issued_access_tokens + fake.issued_id_tokens
    assert tokens
    kept = repr(identity) + repr(vars(provider))
    for token in tokens:
        assert token not in kept


@pytest.mark.parametrize("failure", ["provider_down", "wrong_verifier", "replayed_code"])
async def test_token_endpoint_failure_is_provider_error(
    provider: GoogleOIDCProvider, fake: FakeGoogle, failure: str
) -> None:
    verifier = new_code_verifier()
    url = await provider.authorization_url(
        state="s", nonce="n", code_challenge=code_challenge_s256(verifier)
    )
    code = fake.authorize(url, GMAIL_ACCOUNT)
    if failure == "provider_down":
        fake.unavailable = True
    elif failure == "wrong_verifier":
        verifier = new_code_verifier()
    else:
        await provider.exchange(code=code, code_verifier=verifier, nonce="n")
    with pytest.raises(IdentityError) as caught:
        await provider.exchange(code=code, code_verifier=verifier, nonce="n")
    assert caught.value.reason == "provider_error"
