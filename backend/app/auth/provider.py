"""Identity provider interface (OIDC authorization code + PKCE), and PKCE helpers.

The only implementation in `app/` is Google (`app/auth/google.py`). Tests simulate Google at the
HTTP level (`tests/fakes/google.py`); there is no fake provider in the application (FR-6).
"""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Literal, Protocol

IdentityFailure = Literal["email_not_verified", "invalid_id_token", "provider_error"]


@dataclass(frozen=True)
class VerifiedIdentity:
    """Identity proven by a validated ID token. Carries no token of any kind (FR-5)."""

    sub: str
    email: str
    email_verified: bool
    name: str
    picture: str | None


class IdentityProviderUnavailable(Exception):
    """Sign-in cannot start: provider not configured, or unreachable."""


class IdentityError(Exception):
    """The provider's answer is refused; `reason` is safe to log (no secret)."""

    def __init__(self, reason: IdentityFailure) -> None:
        super().__init__(reason)
        self.reason: IdentityFailure = reason


class IdentityProvider(Protocol):
    async def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        """URL of the provider's consent page (the browser is redirected there)."""
        ...

    async def exchange(self, *, code: str, code_verifier: str, nonce: str) -> VerifiedIdentity:
        """Exchange the code (server to server) and validate the ID token, or raise
        `IdentityError`."""
        ...


def new_code_verifier() -> str:
    """PKCE code verifier: 86 URL-safe characters (RFC 7636 allows 43 to 128)."""
    return secrets.token_urlsafe(64)


def code_challenge_s256(code_verifier: str) -> str:
    """PKCE S256 challenge: BASE64URL(SHA256(verifier)), without padding (RFC 7636 §4.2)."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
