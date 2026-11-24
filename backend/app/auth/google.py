"""Google sign-in (OIDC authorization code + PKCE), written with `httpx` and `PyJWT` (plan T8).

Google is reached through an injected `httpx.AsyncClient`: tests give it an
`httpx.MockTransport` that simulates Google; production uses httpx's default transport.
"""

import hmac
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from app.auth.provider import IdentityError, IdentityProviderUnavailable, VerifiedIdentity
from app.core.clock import Clock
from app.core.config import Settings

DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
SCOPE = "openid email profile"  # basic identity only: no Drive, no offline access (FR-4)
ISSUERS = ("https://accounts.google.com", "accounts.google.com")


class GoogleOIDCProvider:
    """`IdentityProvider` for Google. Validation of the ID token: RS256 signature against Google's
    JWKS, `iss`, `aud`, `azp` (if present), `exp` / `iat` (injected clock), `nonce`
    (`hmac.compare_digest`), then `email_verified is True`. The `hd` claim is ignored (FR-1)."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient, clock: Clock) -> None:
        self._settings = settings
        self._http = http
        self._clock = clock
        self._discovery: dict[str, Any] | None = None
        self._jwks: jwt.PyJWKSet | None = None

    def _client_id(self) -> str:
        if not (
            self._settings.google_client_id
            and self._settings.google_client_secret
            and self._settings.google_client_secret.get_secret_value()
            and self._settings.oidc_redirect_uri
        ):
            raise IdentityProviderUnavailable(
                "Google sign-in is not configured: set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and "
                "OIDC_REDIRECT_URI in the repo-root .env (see .env.example)."
            )
        return self._settings.google_client_id

    async def _get_json(self, url: str) -> dict[str, Any]:
        response = await self._http.get(url)
        response.raise_for_status()
        document: dict[str, Any] = response.json()
        return document

    async def _discovery_document(self) -> dict[str, Any]:
        """Google's OpenID configuration, fetched once and cached."""
        if self._discovery is None:
            self._discovery = await self._get_json(DISCOVERY_URL)
        return self._discovery

    async def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        client_id = self._client_id()
        try:
            endpoint = str((await self._discovery_document())["authorization_endpoint"])
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise IdentityProviderUnavailable("Google discovery document unavailable") from exc
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": self._settings.oidc_redirect_uri,
            "scope": SCOPE,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        return f"{endpoint}?{urlencode(params)}"

    async def exchange(self, *, code: str, code_verifier: str, nonce: str) -> VerifiedIdentity:
        try:
            client_id = self._client_id()
            discovery = await self._discovery_document()
            response = await self._http.post(
                str(discovery["token_endpoint"]),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": code_verifier,
                    "redirect_uri": self._settings.oidc_redirect_uri,
                    "client_id": client_id,
                    "client_secret": self._client_secret(),
                },
            )
            response.raise_for_status()
            # Only the ID token is read; the access token is dropped here, never stored (FR-5).
            id_token = str(response.json()["id_token"])
        except (IdentityProviderUnavailable, httpx.HTTPError, ValueError, KeyError) as exc:
            raise IdentityError("provider_error") from exc

        claims = await self._validated_claims(id_token, client_id=client_id, nonce=nonce)
        if claims.get("email_verified") is not True:  # FR-2; the `hd` claim is ignored (FR-1)
            raise IdentityError("email_not_verified")
        email = claims.get("email")
        if not isinstance(email, str) or not email:
            raise IdentityError("invalid_id_token")
        name = claims.get("name")
        picture = claims.get("picture")
        return VerifiedIdentity(
            sub=str(claims["sub"]),
            email=email,
            email_verified=True,
            name=name if isinstance(name, str) and name else email,
            picture=picture if isinstance(picture, str) and picture else None,
        )

    def _client_secret(self) -> str:
        secret = self._settings.google_client_secret
        assert secret is not None  # checked by `_client_id`
        return secret.get_secret_value()

    async def _signing_key(self, kid: str) -> jwt.PyJWK:
        """Key `kid` of Google's JWKS. The JWKS is cached, and reloaded once when `kid` is unknown
        (Google rotates its keys)."""
        reloaded = False
        while True:
            if self._jwks is None or reloaded:
                discovery = await self._discovery_document()
                self._jwks = jwt.PyJWKSet.from_dict(await self._get_json(discovery["jwks_uri"]))
                fresh = True
            else:
                fresh = False
            for key in self._jwks.keys:
                if key.key_id == kid:
                    return key
            if fresh:
                raise IdentityError("invalid_id_token")
            reloaded = True

    async def _validated_claims(
        self, id_token: str, *, client_id: str, nonce: str
    ) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
            kid = header.get("kid")
            if not isinstance(kid, str):
                raise IdentityError("invalid_id_token")
            key = await self._signing_key(kid)
            claims = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=client_id,
                issuer=ISSUERS,
                # `exp` / `iat` are checked below against the injected clock (plan T14): PyJWT
                # would compare them with the system time.
                options={
                    "require": ["exp", "iat", "sub", "aud", "iss"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except (jwt.PyJWTError, httpx.HTTPError, ValueError, KeyError) as exc:
            raise IdentityError("invalid_id_token") from exc

        now = self._clock.now().timestamp()
        exp, iat = claims["exp"], claims["iat"]
        if not (isinstance(exp, int) and isinstance(iat, int)) or not (iat <= now < exp):
            raise IdentityError("invalid_id_token")
        if not isinstance(claims["sub"], str) or not claims["sub"]:
            raise IdentityError("invalid_id_token")
        azp = claims.get("azp")
        if azp is not None and azp != client_id:
            raise IdentityError("invalid_id_token")
        token_nonce = claims.get("nonce")
        if not isinstance(token_nonce, str) or not hmac.compare_digest(
            token_nonce.encode(), nonce.encode()
        ):
            raise IdentityError("invalid_id_token")
        return claims
