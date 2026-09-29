# Entra ID On-Behalf-Of MCP (FastMCP)

Example **Model Context Protocol** server that shows how **Microsoft Entra ID On-Behalf-Of (OBO)** works.

The MCP client authenticates to *this server*. The server does **not** forward that token to Microsoft Graph. It uses **MSAL** `acquire_token_on_behalf_of` to mint a new token whose audience is Graph, then calls Graph **as the signed-in user**.

It also runs locally and on **Amazon Bedrock AgentCore Runtime**, deployable with **AWS CDK**.

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
FastMCP server  (local or AgentCore Runtime)
  |  MSAL acquire_token_on_behalf_of
  |  POST /oauth2/v2.0/token
  |    grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer
  |    requested_token_use=on_behalf_of
  |    assertion=A
  |    scope=https://graph.microsoft.com/User.Read
  v
Entra ID  -- access token B (aud = Graph, same user oid, azp = MCP app)
  |
  v
Microsoft Graph  /me  /me/messages
```

Entra OBO is **not** RFC 8693 Token Exchange. It is RFC 7523 `jwt-bearer` plus `requested_token_use=on_behalf_of`.

## Tools

| Tool | What it proves |
| --- | --- |
| `whoami_mcp` | Inbound JWT `aud` is the MCP app |
| `whoami_graph` | OBO minted a *different* token; Graph `/me` returns the same user; claim pair is validated |
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

## 2. Run the server locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env
python server.py
```

Server listens at `http://127.0.0.1:8000/mcp` (streamable HTTP).

### Auth modes

| `AUTH_MODE` | When to use it |
| --- | --- |
| `bearer` (default) | You already have an Entra access token for `api://<app-id>/access_as_user`. Used by `client_demo.py`. |
| `proxy` | MCP hosts that speak the MCP OAuth spec and expect Dynamic Client Registration. FastMCP's `AzureProvider` proxies Entra. |
| `agentcore` | Amazon Bedrock AgentCore Runtime validates the Entra JWT at the edge. FastMCP does not attach a second verifier. |

## 3. Call it with the demo client

In a second terminal, same `.venv` and `.env`:

```bash
python client_demo.py
```

Complete the device-code login in a browser. The client requests **only** the MCP scope. Graph access happens later, inside the server, via OBO.

You should see `whoami_mcp` report `audience_matches_mcp_app: true`, then `whoami_graph` report a Graph `aud`, the same `oid`, and `claim_validation.ok: true`.

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

## 4. OBO claim validation

After MSAL returns the Graph token, `EntraOboClient.validate_obo_pair` checks:

| Check | Expect |
| --- | --- |
| inbound `idtyp` | not `app` |
| inbound vs Graph `oid` | same user |
| inbound vs Graph `tid` | same tenant |
| Graph `aud` | `https://graph.microsoft.com` or Graph app id `00000003-0000-0000-c000-000000000000` |
| Graph `azp` | this MCP app client id |

Inbound signature / issuer / lifetime for `AUTH_MODE=bearer` is FastMCP `AzureJWTVerifier`. On AgentCore that job is the platform custom JWT authorizer. `inspect_assertion` is decode-only and is not the security gate.

## 5. Deploy to Amazon Bedrock AgentCore with AWS CDK

AgentCore hosts FastMCP when the container listens on `0.0.0.0:8000` and serves `POST /mcp` over stateless streamable HTTP. The image is **linux/arm64**.

Inbound auth is Entra v2 OIDC:

`https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration`

`allowedAudience` is the MCP app client id / `api://<client-id>`. The runtime allowlists `Authorization` so the same user token reaches MSAL OBO.

### Prerequisites

- AWS CDK v2 (`npm i -g aws-cdk`), Docker, AWS credentials
- Entra app from section 1
- A Secrets Manager secret whose string is the Entra client secret (plain string or JSON with `client_secret`)

```bash
aws secretsmanager create-secret \
  --name entra-obo-mcp/client-secret \
  --secret-string 'your-entra-client-secret'
```

### Deploy

```bash
cd infrastructure/cdk
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cdk bootstrap   # first time in the account/region

cdk deploy EntraOboMcpStack \
  -c entra_tenant_id=<TENANT_ID> \
  -c entra_client_id=<CLIENT_ID> \
  -c entra_identifier_uri=api://<CLIENT_ID> \
  -c entra_secret_name=entra-obo-mcp/client-secret
```

Outputs include `AgentRuntimeArn` and `InvokeUrl`:

`https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<url-encoded-arn>/invocations?qualifier=DEFAULT`

Point `client_demo.py` at that URL by setting `MCP_BASE_URL` to the invoke URL **without** a trailing `/mcp` if the AgentCore data plane already maps `/invocations` to the container `/mcp`. If your client requires an explicit `/mcp` path, append it and test with MCP Inspector.

```bash
export MCP_BASE_URL='https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/<encoded-arn>/invocations?qualifier=DEFAULT'
export AUTH_MODE=bearer   # client still uses device code against Entra
python client_demo.py
```

The runtime itself must run with `AUTH_MODE=agentcore` (the Dockerfile and CDK stack set that).

## Why the extra hop

- Tokens are resource-bound. Graph will not accept a token whose `aud` is your MCP app.
- Forwarding the user's raw token is a confused-deputy risk and breaks Conditional Access / token binding.
- OBO keeps `oid` / `sub` as the user and records the MCP app as `azp`.
- Consent for Graph lives on the **MCP app registration**, not on whatever host launched the agent.
- AgentCore Identity validating the inbound JWT is not a substitute for Entra OBO.

## Common errors

| Symptom | Cause |
| --- | --- |
| `AADSTS50013` | Assertion `aud` is Graph or some other API. Get a token for `api://<mcp-app>/access_as_user`. |
| `AADSTS65001` / `interaction_required` / `IDW10502` | No delegated grant for Graph on this app. Admin-consent `User.Read` / `Mail.Read`. |
| `AADSTS70011` | Mixed `.default` with named scopes in one OBO call. |
| Guest user gets a token for the wrong tenant | OBO used `/common`. This server uses the `tid` claim from the inbound token. |
| OBO on a daemon token | OBO is user-delegation only. App-only traffic should use client credentials. |
| No bearer token on AgentCore | Runtime did not allowlist `Authorization`. |
| Image pull / exec format error | Image was not built for `linux/arm64`. |

## Code map

- `server.py` — FastMCP tools and auth wiring (`bearer` / `proxy` / `agentcore`)
- `obo.py` — MSAL `acquire_token_on_behalf_of`, audience check, claim pair validation, Graph calls
- `client_demo.py` — device-code login for the MCP audience, then tool calls
- `config.py` — environment, including Secrets Manager secret ARN
- `Dockerfile` — AgentCore contract (`0.0.0.0:8000`, `AUTH_MODE=agentcore`)
- `infrastructure/cdk` — ARM64 image, IAM role, `CfnRuntime` with Entra custom JWT

The raw Entra form MSAL sends:

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
