"""Environment configuration for the Entra ID OBO MCP example."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

AUTH_MODES = {"bearer", "proxy", "agentcore"}


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable {name}. "
            "Copy .env.example to .env and fill in your Entra app registration."
        )
    return value


def _client_secret() -> str:
    arn = os.getenv("ENTRA_CLIENT_SECRET_ARN", "").strip()
    if arn:
        import boto3

        raw = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if isinstance(parsed, dict):
            return (
                parsed.get("ENTRA_CLIENT_SECRET")
                or parsed.get("client_secret")
                or parsed.get("value")
                or raw
            )
        return raw
    return _required("ENTRA_CLIENT_SECRET")


@dataclass(frozen=True)
class Settings:
    tenant_id: str
    client_id: str
    client_secret: str
    identifier_uri: str
    mcp_scope: str
    base_url: str
    auth_mode: str
    host: str
    port: int
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

    @property
    def oidc_discovery_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0/.well-known/openid-configuration"


def load_settings() -> Settings:
    client_id = _required("ENTRA_CLIENT_ID")
    identifier_uri = os.getenv("ENTRA_IDENTIFIER_URI", "").strip() or f"api://{client_id}"
    graph = os.getenv(
        "GRAPH_SCOPES",
        "https://graph.microsoft.com/User.Read https://graph.microsoft.com/Mail.Read",
    )
    mode = os.getenv("AUTH_MODE", "bearer").strip().lower()
    if mode not in AUTH_MODES:
        raise RuntimeError("AUTH_MODE must be 'bearer', 'proxy', or 'agentcore'")

    return Settings(
        tenant_id=_required("ENTRA_TENANT_ID"),
        client_id=client_id,
        client_secret=_client_secret(),
        identifier_uri=identifier_uri,
        mcp_scope=os.getenv("ENTRA_MCP_SCOPE", "access_as_user").strip(),
        base_url=os.getenv("MCP_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
        auth_mode=mode,
        host=os.getenv("HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=int(os.getenv("PORT", "8000")),
        graph_scopes=tuple(scope for scope in graph.split() if scope),
    )
