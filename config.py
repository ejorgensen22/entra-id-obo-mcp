"""Environment configuration for the Entra ID OBO MCP example."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable {name}. "
            "Copy .env.example to .env and fill in your Entra app registration."
        )
    return value


@dataclass(frozen=True)
class Settings:
    tenant_id: str
    client_id: str
    client_secret: str
    identifier_uri: str
    mcp_scope: str
    base_url: str
    auth_mode: str
    graph_scopes: tuple[str, ...]

    @property
    def mcp_scope_full(self) -> str:
        return f"{self.identifier_uri}/{self.mcp_scope}"

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    @property
    def token_endpoint(self) -> str:
        return f"{self.authority}/oauth2/v2.0/token"


def load_settings() -> Settings:
    client_id = _required("ENTRA_CLIENT_ID")
    identifier_uri = os.getenv("ENTRA_IDENTIFIER_URI", "").strip() or f"api://{client_id}"
    graph = os.getenv(
        "GRAPH_SCOPES",
        "https://graph.microsoft.com/User.Read https://graph.microsoft.com/Mail.Read",
    )
    mode = os.getenv("AUTH_MODE", "bearer").strip().lower()
    if mode not in {"bearer", "proxy"}:
        raise RuntimeError("AUTH_MODE must be 'bearer' or 'proxy'")

    return Settings(
        tenant_id=_required("ENTRA_TENANT_ID"),
        client_id=client_id,
        client_secret=_required("ENTRA_CLIENT_SECRET"),
        identifier_uri=identifier_uri,
        mcp_scope=os.getenv("ENTRA_MCP_SCOPE", "access_as_user").strip(),
        base_url=os.getenv("MCP_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
        auth_mode=mode,
        graph_scopes=tuple(scope for scope in graph.split() if scope),
    )
