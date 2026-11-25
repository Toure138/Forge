"""Fake Google (OIDC provider) for tests, served through `httpx.MockTransport(fake.handler)`.

It lives in `tests/` only: `app/` never contains a fake identity provider (FR-6, AC-7).

What it does, like Google:
- serves the discovery document and the JWKS (RSA key generated per instance, `rotate_key()`);
- `authorize(authorization_url, account)` plays the user's consent: it reads the parameters the
  browser would carry to Google and returns a single-use authorization code;
- the token endpoint checks the client credentials, the redirect URI, the code (single use) and
  the PKCE verifier (S256), then returns an ID token signed for the account, and an access token.

Knobs: `unavailable` (every request fails as unreachable), `claim_overrides` (claims forced into
the next ID tokens: wrong audience, issuer, nonce, expired...), `sign_with_foreign_key`.
Every request is recorded in `requests`; tokens and verifiers it saw are kept so that tests can
check they never leak (`issued_id_tokens`, `issued_access_tokens`, `received_code_verifiers`).
"""

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.auth.provider import code_challenge_s256
from tests.fakes.clock import FakeClock

ISSUER = "https://accounts.google.com"
DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
CLIENT_SECRET = "test-client-secret"
ID_TOKEN_LIFETIME = timedelta(hours=1)


@dataclass(frozen=True)
class GoogleAccount:
    sub: str
    email: str
    name: str
    picture: str | None = None
    email_verified: bool = True
    hd: str | None = None  # Google Workspace domain; absent for Gmail accounts


WORKSPACE_ACCOUNT = GoogleAccount(
    sub="100000000000000000001",
    email="alice@acme.example",
    name="Alice Martin",
    picture="https://lh3.googleusercontent.com/a/alice",
    hd="acme.example",
)
GMAIL_ACCOUNT = GoogleAccount(
    sub="100000000000000000002",
    email="bob.durand@gmail.com",
    name="Bob Durand",
    picture="https://lh3.googleusercontent.com/a/bob",
)


@dataclass
class _Grant:
    account: GoogleAccount
    client_id: str
    redirect_uri: str
    nonce: str
    code_challenge: str
    code_challenge_method: str
    used: bool = False


def _new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class FakeGoogle:
    clock: FakeClock = field(default_factory=FakeClock)
    client_id: str = CLIENT_ID
    client_secret: str = CLIENT_SECRET
    unavailable: bool = False
    claim_overrides: dict[str, Any] = field(default_factory=dict)
    sign_with_foreign_key: bool = False
    requests: list[httpx.Request] = field(default_factory=list)
    issued_codes: list[str] = field(default_factory=list)
    issued_id_tokens: list[str] = field(default_factory=list)
    issued_access_tokens: list[str] = field(default_factory=list)
    received_code_verifiers: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._key_number = 1
        self.kid = "fake-key-1"
        self.private_key = _new_key()
        self._grants: dict[str, _Grant] = {}

    # --- what Google publishes ---

    def discovery_document(self) -> dict[str, Any]:
        return {
            "issuer": ISSUER,
            "authorization_endpoint": AUTHORIZATION_ENDPOINT,
            "token_endpoint": TOKEN_ENDPOINT,
            "jwks_uri": JWKS_URI,
            "response_types_supported": ["code"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["plain", "S256"],
        }

    def jwks(self) -> dict[str, Any]:
        jwk: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(self.private_key.public_key()))
        jwk.update({"kid": self.kid, "alg": "RS256", "use": "sig"})
        return {"keys": [jwk]}

    def rotate_key(self) -> None:
        """Google rotates its signing keys: new key, new `kid`; the old one is withdrawn."""
        self._key_number += 1
        self.kid = f"fake-key-{self._key_number}"
        self.private_key = _new_key()

    def count(self, url: str) -> int:
        """Number of requests received for `url` (scheme, host and path; query ignored)."""
        return sum(1 for r in self.requests if str(r.url.copy_with(query=None)) == url)

    # --- the user at Google's consent page ---

    def authorize(self, authorization_url: str, account: GoogleAccount) -> str:
        """`account` consents on the page at `authorization_url`; returns the code that Google
        would put in the redirect to the callback."""
        parts = urlsplit(authorization_url)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == AUTHORIZATION_ENDPOINT
        params = {k: v[0] for k, v in parse_qs(parts.query).items()}
        assert params["response_type"] == "code"
        assert params["client_id"] == self.client_id
        code = secrets.token_urlsafe(24)
        self._grants[code] = _Grant(
            account=account,
            client_id=params["client_id"],
            redirect_uri=params["redirect_uri"],
            nonce=params["nonce"],
            code_challenge=params["code_challenge"],
            code_challenge_method=params["code_challenge_method"],
        )
        self.issued_codes.append(code)
        return code

    # --- tokens ---

    def id_token_claims(self, grant: _Grant) -> dict[str, Any]:
        now = int(self.clock.now().timestamp())
        account = grant.account
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "azp": grant.client_id,
            "aud": grant.client_id,
            "sub": account.sub,
            "email": account.email,
            "email_verified": account.email_verified,
            "name": account.name,
            "iat": now,
            "exp": now + int(ID_TOKEN_LIFETIME.total_seconds()),
            "nonce": grant.nonce,
        }
        if account.picture is not None:
            claims["picture"] = account.picture
        if account.hd is not None:
            claims["hd"] = account.hd
        return claims | self.claim_overrides

    def sign(self, claims: dict[str, Any]) -> str:
        key = _new_key() if self.sign_with_foreign_key else self.private_key
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": self.kid})

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.received_code_verifiers.append(form.get("code_verifier", ""))
        grant = self._grants.get(form.get("code", ""))
        if (
            form.get("grant_type") != "authorization_code"
            or form.get("client_id") != self.client_id
            or form.get("client_secret") != self.client_secret
        ):
            return httpx.Response(401, json={"error": "invalid_client"})
        if (
            grant is None
            or grant.used
            or form.get("redirect_uri") != grant.redirect_uri
            or grant.code_challenge_method != "S256"
            or code_challenge_s256(form.get("code_verifier", "")) != grant.code_challenge
        ):
            return httpx.Response(400, json={"error": "invalid_grant"})
        grant.used = True  # single use, like Google
        id_token = self.sign(self.id_token_claims(grant))
        access_token = f"ya29.fake-{secrets.token_urlsafe(24)}"
        self.issued_id_tokens.append(id_token)
        self.issued_access_tokens.append(access_token)
        body = {
            "access_token": access_token,
            "expires_in": 3599,
            "scope": "openid https://www.googleapis.com/auth/userinfo.email",
            "token_type": "Bearer",
            "id_token": id_token,
        }
        return httpx.Response(200, json=body)

    # --- httpx.MockTransport handler ---

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.unavailable:
            raise httpx.ConnectError("fake Google is unavailable", request=request)
        routes: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {
            ("GET", DISCOVERY_URL): lambda _: httpx.Response(200, json=self.discovery_document()),
            ("GET", JWKS_URI): lambda _: httpx.Response(200, json=self.jwks()),
            ("POST", TOKEN_ENDPOINT): self._token,
        }
        route = routes.get((request.method, str(request.url.copy_with(query=None))))
        if route is None:
            return httpx.Response(404, json={"error": "not_found"})
        return route(request)
