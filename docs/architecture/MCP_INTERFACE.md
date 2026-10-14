# SourceMind MCP interface (read-only, first version)

Status: implemented in this repository, **off by default** (`MCP_ENABLED=false`). Not deployed, and
the authentication path is **not verified against a live identity provider** (see "Verification").

## Scope

Two tools, no writes, no resources, no prompts, no autonomous actions:

| Tool | Input | Output |
|---|---|---|
| `search_memories` | `query` (1-500), `workspace_id?`, `mode?` (hybrid/semantic/keyword), `limit?` (1-10, default 5) | `results[]`, `total_found`, `returned`, `notice` |
| `get_memory` | `memory_id` (UUID), `workspace_id?` | `memory`, `is_current`, `latest_memory_id`, `version_history[]`, `notice` |

Each memory carries `memory_id`, `workspace_id`, `content` (untrusted, truncated to 1500 characters in
search and 8000 in `get_memory`, with `content_truncated`), `version`, `category`, `tags`, `created_at`,
`authorship` (`resolved|shared|unresolved`, never a percentage), `contributors`, `source`
(`tool/type/id/url/revision`), `source_state` (`current|stale` with reason and the newer source link) and a
`citation` (`sourcemind://memory/<id>`). These objects are the same ones the REST API returns (one
definition in `services/memory/read.py`).

## Authentication and authorization

* Transport: Streamable HTTP, single endpoint `/mcp`, protocol revision 2026-07-28 and the earlier
  handshake revisions through the official `mcp` Python SDK (pinned `mcp==2.3.0`). Origin and Host are
  validated (`MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS`).
* The server is an OAuth 2.1 **resource server**. It serves protected-resource metadata at
  `/.well-known/oauth-protected-resource/mcp` and answers an unauthenticated request with 401 and a
  `WWW-Authenticate` header naming it.
* A bearer token is accepted only if it is a JWT signed by the configured issuer's keys and its
  **audience contains the canonical resource URL** (`MCP_RESOURCE_URL`). A session token, a token for
  another server, an expired or wrongly signed token all get the same 401. There is no fallback scheme.
* Per call: the token subject must map to an **existing** SourceMind user (nothing is provisioned), the
  database session gets that user's RLS context, and workspace membership with READ is asserted.
  `workspace_id` is a selector only. A workspace or memory the caller cannot access gets the same
  `not_found` result as one that does not exist.
* **Mandatory checks** (a token failing any of them gets the same 401): signature and issuer, **audience**
  equal to the resource URL, expiry, and the **required scope** (`MCP_REQUIRED_SCOPES`, default
  `memories:read`, must be non-empty when the interface is enabled). The audience says the token was
  issued for this server; the scope says the user consented to what it does (read memories).
* **Optional check:** `MCP_ALLOWED_CLIENT_IDS` restricts WHICH OAuth clients may be used. It is not required
  by policy (consent + audience + scope + the per-call membership check already bound what a client can
  reach), is empty by default, and only narrows. Do not read an empty allowlist as "no policy".
* Rate limiting: `search_memories` uses the search limiter; `get_memory` has its own per-user budget
  (`RATE_LIMIT_MCP_READ_PER_MINUTE`, default 120) so an agent looping over ids cannot exhaust the
  interactive search allowance. JWKS refreshes forced by an unknown key id are limited to one per 30 s.
  Logs record user id, workspace id and result counts, never memory text.
* **Revocation (honest status).** Access tokens are JWTs verified offline. Revoking a grant therefore
  does not stop an already issued token until it expires (Clerk documents a one-day lifetime for OAuth
  access tokens). That does NOT meet "revocation takes effect on the next call" for the token itself.
  What does take effect on the next call: removing the user from the workspace, and deleting or
  deactivating the user row (both are checked per call against the database). The smallest supported
  change, to be chosen after the Clerk probe (`operator/clerk_compat.py`), is one of: (a) a shorter
  access-token lifetime configured in Clerk if the dashboard allows it, or (b) opaque access tokens
  verified by a call to Clerk per request (needs the verify endpoint's documented response, plus latency
  and an availability dependency, so it changes this contract and needs your approval). Until one is
  measured and accepted, keep MCP disabled.

## Untrusted content

Retrieved text is team data from external sources and may contain instructions. Responses label it
(`content_is_untrusted`, `notice`) and the tool descriptions say not to follow it. This is a mitigation,
not a guarantee; the tools are read-only, which bounds what an injected instruction could do.

## Errors

Authentication failures are HTTP 401. Tool failures are `isError` results whose text contains a stable
code: `not_found`, `unauthorized` (account not set up), `invalid_argument`, `workspace_required`
(several workspaces; the message lists the caller's own), `rate_limited`.

## Configuration

`MCP_ENABLED`, `MCP_RESOURCE_URL` (required when enabled, e.g. `https://api.example.com/mcp`),
`MCP_ISSUER_URL` (defaults to the Clerk Frontend API host derived from `CLERK_PUBLISHABLE_KEY`),
`MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS`, `MCP_REQUIRED_SCOPES`.

## Verification (what is and is not established)

Executed in CI-equivalent local runs (CPython 3.12, real PostgreSQL 18 under FORCE RLS as the runtime
role, official MCP client in the 2026-07-28 and handshake modes): tool listing, search and get with
citations, cross-workspace denial with an identical outcome for missing/forbidden, unknown and
non-member accounts, membership removal effective on the next call, token rules (wrong audience,
audience of the API root, wrong issuer, expiry, foreign signature, unknown key, garbage, none), Origin/
Host/method rules, bounded payloads.

**Not established:** that the identity provider (Clerk) issues access tokens whose audience can be set to
the MCP resource, how its grants are revoked, whether this works on a development instance, or that
Claude Code completes the OAuth flow against it. Until the owner's live checks (decision log D-033) pass,
this interface must not be enabled in production; the verifier fails closed (a token without the
resource audience is rejected). No alternative credential scheme is implemented.
