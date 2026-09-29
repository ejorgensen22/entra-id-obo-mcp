"""FastMCP server that validates an Entra token for this app, then OBO-exchanges it for Graph."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token

from config import load_settings
from obo import EntraOboClient, OboError

settings = load_settings()
obo = EntraOboClient(settings)


def build_auth():
    """Create FastMCP auth.

    bearer    — RemoteAuthProvider + AzureJWTVerifier. Clients present an Entra JWT
                whose audience is this MCP app. Best for the device-code demo client.
    proxy     — AzureProvider OAuth proxy. FastMCP stands in front of Entra so MCP
                clients that expect Dynamic Client Registration can still sign in.
    agentcore — No in-process verifier. Amazon Bedrock AgentCore Runtime validates
                the Entra JWT at the edge and forwards Authorization to this process.
    """
    if settings.auth_mode == "agentcore":
        return None

    if settings.auth_mode == "proxy":
        from fastmcp.server.auth.providers.azure import AzureProvider

        return AzureProvider(
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            tenant_id=settings.tenant_id,
            base_url=settings.base_url,
            identifier_uri=settings.identifier_uri,
            required_scopes=[settings.mcp_scope],
            additional_authorize_scopes=["User.Read", "Mail.Read", "offline_access"],
        )

    from fastmcp.server.auth import RemoteAuthProvider
    from fastmcp.server.auth.providers.azure import AzureJWTVerifier

    verifier = AzureJWTVerifier(
        client_id=settings.client_id,
        tenant_id=settings.tenant_id,
        required_scopes=[settings.mcp_scope],
    )
    return RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[settings.issuer],
        base_url=settings.base_url,
        resource_name="Entra OBO MCP example",
    )


mcp = FastMCP(
    name="entra-id-obo-mcp",
    instructions=(
        "Demo MCP server for Microsoft Entra ID On-Behalf-Of. "
        "whoami_mcp shows the inbound MCP-audience token. "
        "whoami_graph and list_recent_mail exchange that token for Graph and "
        "call Microsoft Graph as the signed-in user."
    ),
    auth=build_auth(),
    host=settings.host,
    port=settings.port,
    stateless_http=True,
)


def _inbound_token() -> str:
    token = get_access_token()
    if token is None or not getattr(token, "token", None):
        raise OboError(
            "No bearer token on this request. Send an Entra access token for "
            f"{settings.mcp_scope_full}. On AgentCore, allowlist the Authorization header."
        )
    return token.token


@mcp.tool
def whoami_mcp() -> dict[str, Any]:
    """Show claims from the inbound token (audience should be this MCP app, not Graph)."""
    assertion = _inbound_token()
    claims = obo.inspect_assertion(assertion)
    expected = obo.expected_mcp_audiences()
    audiences = set(claims["aud"])
    return {
        "step": "1. inbound token",
        "auth_mode": settings.auth_mode,
        "audience_matches_mcp_app": bool(audiences & expected),
        "expected_audiences": sorted(expected),
        "claims": claims,
        "note": (
            "This token must NOT be forwarded to Graph. The next tools call "
            "Entra OBO and use a *new* token whose aud is graph.microsoft.com."
        ),
    }


@mcp.tool
async def whoami_graph() -> dict[str, Any]:
    """Exchange the MCP token for a Graph token and GET /me as the signed-in user."""
    assertion = _inbound_token()
    inbound = obo.inspect_assertion(assertion)
    profile = await obo.graph_get(
        assertion,
        "/me?$select=id,displayName,userPrincipalName,mail,jobTitle,officeLocation",
        scopes=["https://graph.microsoft.com/User.Read"],
    )
    graph_token = obo.exchange(assertion, ["https://graph.microsoft.com/User.Read"])
    pair = obo.validate_obo_pair(assertion, graph_token)
    return {
        "step": "2. OBO exchange then Graph /me",
        "inbound_aud": inbound["aud"],
        "claim_validation": pair,
        "same_user_oid": inbound.get("oid") == profile.get("id"),
        "graph_profile": profile,
        "protocol": obo.raw_form_body(),
    }


@mcp.tool
async def list_recent_mail(top: int = 5) -> dict[str, Any]:
    """List recent mailbox messages for the signed-in user via OBO + Mail.Read."""
    assertion = _inbound_token()
    top = max(1, min(int(top), 15))
    payload = await obo.graph_get(
        assertion,
        f"/me/messages?$top={top}&$select=subject,from,receivedDateTime,webLink",
        scopes=["https://graph.microsoft.com/Mail.Read"],
    )
    messages = []
    for item in payload.get("value", []):
        sender = (item.get("from") or {}).get("emailAddress") or {}
        messages.append(
            {
                "subject": item.get("subject"),
                "from": sender.get("address") or sender.get("name"),
                "received": item.get("receivedDateTime"),
                "web_link": item.get("webLink"),
            }
        )
    return {
        "step": "3. OBO with Mail.Read, then Graph /me/messages",
        "count": len(messages),
        "messages": messages,
        "note": (
            "If this fails with AADSTS65001, grant admin consent for Mail.Read "
            "on the MCP app registration."
        ),
    }


@mcp.tool
def explain_obo() -> dict[str, Any]:
    """Explain the Entra OBO hop this server performs."""
    return {
        "what": "On-Behalf-Of (OBO) is Entra's delegated token exchange.",
        "why": (
            "Tokens are audience-bound. A token issued for this MCP app is not "
            "valid at Microsoft Graph. OBO mints a new token for Graph while "
            "keeping the original user's oid."
        ),
        "grant": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "requested_token_use": "on_behalf_of",
        "token_endpoint": settings.token_endpoint,
        "inbound_scope": settings.mcp_scope_full,
        "downstream_scopes": list(settings.graph_scopes),
        "form": obo.raw_form_body(),
        "auth_mode": settings.auth_mode,
        "rules": [
            "assertion.aud must be this MCP app (AADSTS50013 otherwise)",
            "assertion must be a user token, not app-only",
            "Graph delegated permissions must already be consented",
            "use the tenant id from the token tid claim (guests)",
            "never return the Graph OBO token to the MCP client",
            "on AgentCore, allowlist Authorization so OBO still receives the assertion",
        ],
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host=settings.host, port=settings.port)
