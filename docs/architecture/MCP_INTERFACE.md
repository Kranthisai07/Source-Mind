# SourceMind MCP interface (read-only)

Status: implemented, **off by default** (`MCP_ENABLED=false`), **not deployed**. Two authentication modes
exist (`MCP_AUTH_MODE`): `api_key` (workspace MCP keys, decision D-048) and `oauth` (Clerk-issued tokens,
decision D-033, the default value). Key mode was verified locally with a real Claude Code client (see
"Verification"). OAuth mode has **not** been verified against a live identity provider and, as measured on
2026-10-14, Clerk cannot meet the contract (see "Authentication: OAuth mode").

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

Transport: Streamable HTTP, single endpoint `/mcp`, protocol revision 2026-07-28 and the earlier handshake
revisions through the official `mcp` Python SDK (pinned `mcp==2.3.0`). Origin and Host are validated
(`MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS`).

## Authentication: API-key mode (`MCP_AUTH_MODE=api_key`)

A workspace member creates a key in **Settings > API Keys** and gives it to the client as a bearer header.

* **Key.** `smk_` plus 43 URL-safe characters (256 bits from `secrets.token_urlsafe`). Shown **once**, at
  creation. Only its SHA-256 is stored (the key is high-entropy, so a slow hash adds nothing); the secret is
  never logged, returned by the list endpoint, or kept anywhere else.
* **Whose access.** A key acts as the member who created it, **read-only**, in **exactly one workspace**.
  Any active member may create their own key; owners and admins can list and revoke every key in the
  workspace, members only their own. Expiry is mandatory (30, 90 or 365 days; default 90).
* **Per request, no cache.** The middleware (`sourcemind/mcp/keyauth.py`) resolves the key against
  PostgreSQL on every request to `/mcp`. The lookup policy exposes at most one live row (a hash-gated
  row-level-security policy), never a revoked or expired key. Revocation, expiry and replacement therefore
  apply to the very next call.
* **Then the normal read path.** The tools run the existing per-call checks as the creating member: active
  workspace membership, READ permission, workspace not deleted, row-level security. A member who leaves the
  workspace stops working on the next call. A key is forced to its own workspace: another `workspace_id`
  selector, or a memory in another workspace, is `not_found` even if the creator belongs to both.
* **Failure behaviour.** A missing, malformed, oversized, duplicated, unknown, revoked or expired credential
  is the same HTTP 401 (`{"error":"invalid_token"}`) with no OAuth discovery hints; nothing says which.
  A database failure is **503** with `Retry-After`, never 401 (a temporary fault must not look like an
  invalid credential).
* **Not an OAuth server.** No authorization server or protected-resource metadata is advertised in this mode
  (`/.well-known/oauth-protected-resource/mcp` is 404). A client that gets a 401 shows its own
  "authentication failed" message; real Claude Code did not start an OAuth flow.
* **Keys work only on `/mcp`.** Normal REST routes accept Clerk session tokens only and answer a key with 401.
* **Rate limits.** Each key has its own read budget (`RATE_LIMIT_MCP_KEY_READ_PER_MINUTE`, default 120, keyed
  by key id) so an agent loop cannot drain the member's interactive budget; the shared search path still
  applies the member's search limiter on top. Creating keys is limited per member
  (`RATE_LIMIT_MCP_KEY_CREATE_PER_HOUR`, default 10). Limiters fail closed.
* **Audit and logs.** Creation and revocation are written to an append-only table (`mcp_key_events`).
  Request logs carry the key **id**, user id, workspace id, tool and result counts, never the key, its
  hash, the query or memory text. `last_used_at` is refreshed at most every five minutes.
* **Irreversible revocation (database layer).** A trigger forbids changing `revoked_at` once set and
  rewriting `revoked_by`. This closes a path found in review (permissive update policies are ORed, so with
  both the member context and the hash context set a revoked row could otherwise be reactivated). No HTTP
  route could do this; it is defence in depth.

### Threat model (what a key does and does not protect)

A stolen key lets its holder read that member's accessible memories **in that one workspace** until the key is
revoked or expires. Mitigations: mandatory expiry, one-click revocation effective on the next call, read-only
tools, one workspace per key, rate limits, audit events, the `smk_` prefix for secret scanners. It does not
protect against a holder who copies memory content out, and a key on disk in a client config file is only as
safe as that file. Treat it like a password.

### Client setup

Tested against this server (local, loopback): **Claude Code**.

```
claude mcp add --transport http <name> https://<your-api-host>/mcp --header "Authorization: Bearer <key>"
```

Cursor (`headers` in its MCP config) and Codex (`bearer_token_env_var`) are expected to work because they can
send a static bearer header, but **have not been tested** against this server.

## Authentication: OAuth mode (`MCP_AUTH_MODE=oauth`, the default value)

Kept as written for D-033; **do not enable it**: the identity provider cannot meet the contract.

* The server is an OAuth 2.1 **resource server**. It serves protected-resource metadata at
  `/.well-known/oauth-protected-resource/mcp` and answers an unauthenticated request with 401 and a
  `WWW-Authenticate` header naming it.
* A bearer token is accepted only if it is a JWT signed by the configured issuer's keys and its **audience
  contains the canonical resource URL** (`MCP_RESOURCE_URL`); signature, issuer, expiry and the **required
  scope** (`MCP_REQUIRED_SCOPES`, default `memories:read`) are mandatory. Any failure is the same 401. Only
  JWT access tokens are supported.
* Per call: the token subject must map to an **existing** SourceMind user (nothing is provisioned), the
  database session gets that user's RLS context, and workspace membership with READ is asserted.
  `MCP_ALLOWED_CLIENT_IDS` optionally narrows which OAuth clients may be used.
* **Measured limits (2026-10-14, Clerk development instance):** its tokens carry no audience, cannot carry a
  custom scope, and JWT access tokens cannot be revoked (they are verified offline until expiry). So the
  mandatory audience and scope checks cannot be satisfied and "revocation takes effect on the next call"
  does not hold for the token. Real Claude Code failed at sign-in with `invalid_scope`. A Cloudflare Worker
  authorization front door was spiked on 2026-10-16 and worked, but it adds a second runtime and left
  cross-location revocation unmeasured; it was not adopted (D-048).

## Revocation (honest status)

* **Key mode: next-call revocation, measured.** See "Verification". A key revoked in the UI or API is rejected
  from its next request; removing the creator from the workspace also takes effect on the next call.
* **OAuth mode: not met** (above).

## Untrusted content

Retrieved text is team data from external sources and may contain instructions. Responses label it
(`content_is_untrusted`, `notice`) and the tool descriptions say not to follow it. This is a mitigation, not
a guarantee; the tools are read-only, which bounds what an injected instruction could do.

## Errors

Authentication failures are HTTP 401 (503 with `Retry-After` when the key lookup itself is unavailable). Tool
failures are `isError` results whose text contains a stable code: `not_found`, `unauthorized` (OAuth mode:
account not set up), `invalid_argument`, `workspace_required` (OAuth mode: several workspaces), `rate_limited`,
and a fixed `internal_error` for anything unmapped.

## Configuration

`MCP_ENABLED` (default false), `MCP_AUTH_MODE` (`oauth` | `api_key`), `MCP_RESOURCE_URL` (required when
enabled, e.g. `https://api.example.com/mcp`), `MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS`. OAuth mode only:
`MCP_ISSUER_URL`, `MCP_REQUIRED_SCOPES`, `MCP_ALLOWED_CLIENT_IDS`. Key REST routes:
`POST|GET /v1/workspaces/{id}/mcp-keys`, `DELETE /v1/workspaces/{id}/mcp-keys/{key_id}`.

## Verification (what is and is not established)

**Automated** (CI-equivalent local runs: real PostgreSQL 18 under FORCE RLS as the restricted runtime role,
official MCP client in the 2026-07-28 and legacy handshake modes): tool listing, search and get with citations,
cross-workspace denial, membership removal effective on the next call, OAuth token rules, Origin/Host/method
rules, bounded payloads; key mode: valid key reads its workspace, a key is bound to one workspace even when
its creator belongs to two, revocation stops a connected client on its next call, creator losing membership
stops the key, every bad credential is the same 401 with nothing advertised, a database failure is 503, the
key table's row-level-security and grants (one live row per hash, sensitive columns not writable, revocation
irreversible, events append-only), and no secret or hash in logs.

**Live, local, 2026-10-16** (journey stack on loopback: real FastAPI, real PostgreSQL under FORCE RLS, real
Claude Code; evidence in the operations folder `auto-ingest/evidence/2026-10-16-mcp-keys-live`):

| Check | Result |
|---|---|
| Real Claude Code connects with a key and `search_memories` returns 5 memories with source links and stale/current state | pass |
| Revoke in the Settings UI, then the next Claude Code call | rejected (401) on the first request after the revoke; Claude Code showed a generic header-rejected message and did not start OAuth |
| Revocation timing (script, one location, revoke by direct database update at t=5 s) | first 401 19 ms after the revoke finished; 21 calls afterwards, none accepted |
| Database stopped | 503 + `Retry-After: 5`, not 401; the same key worked on the first attempt after restart |
| Four bad credentials (none, malformed, unknown, JWT-like) | one identical 401; no OAuth metadata (404) |
| Key on REST routes | 401 on `/v1/workspaces`, `/v1/workspaces/{id}/mcp-keys` and, with a fake key, on three more routes |
| Latency, client side, loopback, 30 calls each | authorized `initialize` p50 25 ms / p95 29 ms; unauthenticated 401 p50 6 ms / p95 8 ms (different work on the two paths, so not a pure authentication cost) |
| `get_memory` with a key (script, real protocol) | pass; **not** exercised through real Claude Code |

**Not established:** Cursor and Codex clients; a reverse proxy or gateway that rewrites or strips the
`Authorization` header; any production or multi-replica deployment (the transport keeps legacy sessions
in-process, so revisit before running several API replicas); live key expiry (automated tests only); any
identity-provider-based (OAuth) path. Until the owner decides to enable it, the interface stays off.
