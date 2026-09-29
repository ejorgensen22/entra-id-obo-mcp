"""Microsoft Entra ID On-Behalf-Of token exchange.

Entra does not implement RFC 8693 Token Exchange. The protocol is:

    POST {tenant}/oauth2/v2.0/token
    grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer
    requested_token_use=on_behalf_of
    assertion=<inbound MCP-audience access token>
    scope=<downstream resource scopes>
    client_id / client_secret  (or a client_assertion)

The inbound assertion MUST have aud = this MCP app. A Graph token cannot
be exchanged for another Graph token.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
import msal

from config import Settings

GRAPH_AUDIENCES = {
    "00000003-0000-0000-c000-000000000000",
    "https://graph.microsoft.com",
}


class OboError(RuntimeError):
    def __init__(self, message: str, *, entra_error: dict[str, Any] | None = None):
        super().__init__(message)
        self.entra_error = entra_error or {}


@dataclass
class CachedToken:
    access_token: str
    expires_at: float
    scopes: tuple[str, ...]


class EntraOboClient:
    """Confidential-client OBO exchanger with a small in-memory cache."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._cache: dict[str, CachedToken] = {}
        self._apps: dict[str, msal.ConfidentialClientApplication] = {}

    def inspect_assertion(self, assertion: str) -> dict[str, Any]:
        claims = jwt.decode(assertion, options={"verify_signature": False})
        aud = claims.get("aud")
        if isinstance(aud, list):
            audiences = aud
        else:
            audiences = [aud] if aud is not None else []
        return {
            "oid": claims.get("oid"),
            "sub": claims.get("sub"),
            "tid": claims.get("tid"),
            "preferred_username": claims.get("preferred_username")
            or claims.get("upn")
            or claims.get("unique_name"),
            "name": claims.get("name"),
            "aud": audiences,
            "scp": claims.get("scp") or claims.get("roles"),
            "azp": claims.get("azp") or claims.get("appid"),
            "iss": claims.get("iss"),
            "exp": claims.get("exp"),
            "ver": claims.get("ver"),
            "idtyp": claims.get("idtyp"),
        }

    def expected_mcp_audiences(self) -> set[str]:
        return {
            self.settings.client_id,
            self.settings.identifier_uri,
            f"api://{self.settings.client_id}",
        }

    def exchange(self, assertion: str, scopes: list[str] | None = None) -> str:
        if not assertion:
            raise OboError("No inbound access token to exchange.")

        target_scopes = tuple(scopes or self.settings.graph_scopes)
        claims = self.inspect_assertion(assertion)
        self._reject_wrong_audience(claims)
        if claims.get("idtyp") == "app":
            raise OboError(
                "Inbound token is app-only (idtyp=app). OBO requires a user access token."
            )

        cache_key = self._cache_key(assertion, target_scopes)
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and cached.expires_at - 60 > time.time():
                return cached.access_token

        tid = claims.get("tid") or self.settings.tenant_id
        app = self._msal_app(str(tid))
        result = app.acquire_token_on_behalf_of(
            user_assertion=assertion,
            scopes=list(target_scopes),
        )
        if "access_token" not in result:
            raise OboError(
                self._format_msal_error(result, claims),
                entra_error=result,
            )

        self.validate_obo_pair(assertion, result["access_token"])

        expires_in = int(result.get("expires_in") or 3600)
        with self._lock:
            self._cache[cache_key] = CachedToken(
                access_token=result["access_token"],
                expires_at=time.time() + expires_in,
                scopes=target_scopes,
            )
        return result["access_token"]

    def validate_obo_pair(self, inbound: str, downstream: str) -> dict[str, Any]:
        """Compare inbound MCP-audience claims to the OBO-issued Graph token."""
        a = self.inspect_assertion(inbound)
        b = self.inspect_assertion(downstream)
        errors: list[str] = []

        if a.get("idtyp") == "app":
            errors.append("inbound is app-only; OBO requires a user token")
        if a.get("oid") and b.get("oid") and a.get("oid") != b.get("oid"):
            errors.append(f"oid mismatch inbound={a.get('oid')} graph={b.get('oid')}")
        if a.get("tid") and b.get("tid") and a.get("tid") != b.get("tid"):
            errors.append(f"tid mismatch inbound={a.get('tid')} graph={b.get('tid')}")

        graph_auds = {str(item) for item in b.get("aud") or []}
        if graph_auds and graph_auds.isdisjoint(GRAPH_AUDIENCES):
            errors.append(f"downstream aud is not Graph: {sorted(graph_auds)}")

        azp = b.get("azp")
        if azp and azp != self.settings.client_id:
            errors.append(f"downstream azp {azp} is not this MCP app {self.settings.client_id}")

        if errors:
            raise OboError("OBO claim validation failed: " + "; ".join(errors))

        return {
            "ok": True,
            "same_user_oid": a.get("oid") == b.get("oid"),
            "same_tid": a.get("tid") == b.get("tid"),
            "inbound_aud": a.get("aud"),
            "graph_aud": b.get("aud"),
            "graph_azp": azp,
            "inbound_scp": a.get("scp"),
            "graph_scp": b.get("scp"),
        }

    async def graph_get(self, assertion: str, path: str, scopes: list[str] | None = None) -> Any:
        token = self.exchange(assertion, scopes)
        url = path if path.startswith("http") else f"https://graph.microsoft.com/v1.0{path}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
            )
        if response.status_code >= 400:
            raise OboError(
                f"Graph {response.status_code} on {path}: {response.text[:800]}"
            )
        return response.json()

    def raw_form_body(self, assertion_placeholder: str = "<inbound-mcp-access-token>") -> str:
        scopes = " ".join(self.settings.graph_scopes)
        return (
            "grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer\n"
            f"client_id={self.settings.client_id}\n"
            "client_secret=***\n"
            f"assertion={assertion_placeholder}\n"
            f"scope={scopes}\n"
            "requested_token_use=on_behalf_of"
        )

    def _msal_app(self, tid: str) -> msal.ConfidentialClientApplication:
        with self._lock:
            app = self._apps.get(tid)
            if app is None:
                app = msal.ConfidentialClientApplication(
                    client_id=self.settings.client_id,
                    client_credential=self.settings.client_secret,
                    authority=f"https://login.microsoftonline.com/{tid}",
                )
                self._apps[tid] = app
            return app

    def _reject_wrong_audience(self, claims: dict[str, Any]) -> None:
        audiences = {str(item) for item in claims.get("aud") or []}
        expected = self.expected_mcp_audiences()
        if audiences and audiences.isdisjoint(expected):
            raise OboError(
                "Inbound token audience is not this MCP app, so Entra will reject "
                f"OBO (AADSTS50013). aud={sorted(audiences)} expected one of "
                f"{sorted(expected)}. Request a token for "
                f"{self.settings.mcp_scope_full} instead of Graph."
            )

    @staticmethod
    def _cache_key(assertion: str, scopes: tuple[str, ...]) -> str:
        digest = hashlib.sha256(assertion.encode("utf-8")).hexdigest()
        return f"{digest}:{' '.join(scopes)}"

    @staticmethod
    def _format_msal_error(result: dict[str, Any], claims: dict[str, Any]) -> str:
        code = result.get("error")
        description = result.get("error_description") or result.get("error") or "unknown"
        hints = []
        text = f"{code}: {description}"
        lowered = text.lower()
        if "aadsts50013" in lowered:
            hints.append("Assertion aud must be the MCP app, not Graph.")
        if "aadsts65001" in lowered or "interaction_required" in lowered:
            hints.append(
                "Admin or user consent is missing on the MCP app's Graph delegated permissions."
            )
        if "aadsts70011" in lowered:
            hints.append("Do not mix .default with named Graph scopes in one OBO request.")
        if claims.get("idtyp") == "app":
            hints.append("OBO only works for user tokens, not app-only client-credential tokens.")
        suffix = (" Hint: " + " ".join(hints)) if hints else ""
        return f"Entra OBO exchange failed. {text}{suffix}"
