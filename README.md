# Entra ID On-Behalf-Of MCP (FastMCP)

Example **Model Context Protocol** server that shows how **Microsoft Entra ID On-Behalf-Of (OBO)** works.

The MCP client authenticates to *this server*. The server does **not** forward that token to Microsoft Graph. It exchanges it at Entra for a new token whose audience is Graph, then calls Graph **as the signed-in user**.

```
User
  |
  |  device code / MCP OAuth
  v
Entra ID  -- access token A (aud = MCP app, scp = access_as_user)
  |
  v
MCP client (Claude, VS Code, client_demo.py)
  |  Authorization: Bearer A
  v
FastMCP server  (this repo)
  |  POST /oauth2/v2.0/token
  |    grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer
  |    requested_token_use=on_behalf_of
  |    assertion=A
  |    scope=https://graph.microsoft.com/User.Read
  v
Entra ID  -- access token B (aud = Graph, same user oid)
  |
  v
Microsoft Graph  /me  /me/messages
```

Entra OBO is **not** RFC 8693 Token Exchange. It is RFC 7523 `jwt-bearer` plus `requested_token_use=on_behalf_of`.

## Tools

| Tool | What it proves |
| --- | --- |
| `whoami_mcp` | Inbound JWT `aud` is the MCP app |
| `whoami_graph` | OBO minted a *different* token; Graph `/me` returns the same user |
| `list_recent_mail` | Second OBO with `Mail.Read` |
| `explain_obo` | Protocol, endpoint, and the rules that cause AADSTS50013 / 65001 |

## 1. Entra app registration

One confidential client is enough for this example. It plays two roles: **resource** (MCP audience) and **client** (the app that performs OBO).

1. Entra admin center → **App registrations** → **New registration**
   - Name: `entra-id-obo-mcp`
   - Accounts in this organizational directory only
2. **Certificates & secrets** → New client secret. Copy the value.
3. **Expose an API**
   - Application ID URI: accept `api://<application-client-id>`
   - Add scope
     - Name: `access_as_user`
     - Who can consent: Admins and users
     - Admin consent display name / description: allow the MCP server to take actions as the signed-in user
4. **API permissions** → Add a permission → Microsoft Graph → **Delegated**
   - `User.Read`
   - `Mail.Read`
   - Grant **admin consent** for the tenant (required if the MCP client cannot request those Graph scopes at login)
5. **Authentication**
   - Platform: **Mobile and desktop applications**
   - Enable **Allow public client flows** (needed for the device-code demo client)
   - If you use `AUTH_MODE=proxy`, also add a **Web** redirect URI: `http://127.0.0.1:8000/auth/callback`
6. **Manifest** → set `"requestedAccessTokenVersion": 2` inside `api` → Save
7. Copy **Application (client) ID** and **Directory (tenant) ID**

Optional but useful: under **Expose an API** → **Authorized client applications**, add any first-party client you control (VS Code, a custom SPA) so users skip a second consent prompt for `access_as_user`.

## 2. Run the server

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env
python server.py
```

Server listens at `http://127.0.0.1:8000/mcp`.

### Auth modes

| `AUTH_MODE` | When to use it |
| --- | --- |
| `bearer` (default) | You already have an Entra access token for `api://<app-id>/access_as_user`. Used by `client_demo.py`. |
| `proxy` | MCP hosts that speak the MCP OAuth spec and expect Dynamic Client Registration. FastMCP's `AzureProvider` proxies Entra. |

## 3. Call it with the demo client

In a second terminal, same `.venv` and `.env`:

```bash
python client_demo.py
```

Complete the device-code login in a browser. The client requests **only** the MCP scope. Graph access happens later, inside the server, via OBO.

You should see `whoami_mcp` report `audience_matches_mcp_app: true`, then `whoami_graph` report a Graph `aud` and the same `oid`.

### MCP host config (bearer)

```json
{
  "mcpServers": {
    "entra-obo": {
      "url": "http://127.0.0.1:8000/mcp",
      "headers": {
        "Authorization": "Bearer <entra-access-token-for-mcp-app>"
      }
    }
  }
}
```

Do not paste a Graph token into that header. Entra will reject the OBO exchange with `AADSTS50013`.

## Why the extra hop

- Tokens are resource-bound. Graph will not accept a token whose `aud` is your MCP app.
- Forwarding the user's raw token is a confused-deputy risk and breaks Conditional Access / token binding.
- OBO keeps `oid` / `sub` as the user and records the MCP app as `azp`.
- Consent for Graph lives on the **MCP app registration**, not on whatever host launched the agent.

## Common errors

| Symptom | Cause |
| --- | --- |
| `AADSTS50013` | Assertion `aud` is Graph or some other API. Get a token for `api://<mcp-app>/access_as_user`. |
| `AADSTS65001` / `interaction_required` / `IDW10502` | No delegated grant for Graph on this app. Admin-consent `User.Read` / `Mail.Read`. |
| `AADSTS70011` | Mixed `.default` with named scopes in one OBO call. |
| Guest user gets a token for the wrong tenant | OBO used `/common`. This server uses the `tid` claim from the inbound token. |
| OBO on a daemon token | OBO is user-delegation only. App-only traffic should use client credentials. |

## Code map

- `server.py` — FastMCP tools and auth wiring
- `obo.py` — MSAL `acquire_token_on_behalf_of`, audience check, cache, Graph calls
- `client_demo.py` — device-code login for the MCP audience, then tool calls
- `config.py` — environment

The raw Entra form this server sends:

```
POST https://login.microsoftonline.com/{tid}/oauth2/v2.0/token
Content-Type: application/x-www-form-urlencoded

grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer
&client_id={mcp-app-id}
&client_secret=***
&assertion={inbound-mcp-access-token}
&scope=https://graph.microsoft.com/User.Read
&requested_token_use=on_behalf_of
```
