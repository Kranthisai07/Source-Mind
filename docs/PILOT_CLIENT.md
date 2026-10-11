# Pilot Client Usage

The bounded pilot client is inactive by default. Build the isolated pilot frontend with:

```text
REACT_APP_PILOT_MODE=true
REACT_APP_USE_MOCKS=false
REACT_APP_BACKEND_URL=https://<isolated-pilot-api>
REACT_APP_DEFAULT_WORKSPACE_ID=<isolated-pilot-workspace-uuid>
```

These are build-time Create React App values. Do not use the production API, database, Redis, frontend URL, or Clerk application for the pilot.

## Participant workflow

1. Open **Memories**, choose **Ingest**, and confirm the panel says that pilot verbatim mode is active.
2. Enter the complete memory text, tags, and category. Pilot mode sends `ingestion_mode: "verbatim"`, so the backend stores one memory without fact extraction.
3. Open a memory and choose **Edit**. Saving calls the existing PATCH endpoint, which creates the next chronological version. The client reports saving, failure, and successful version creation.
4. Participants must not use `/pilot`. They do not receive rating or export controls, and the backend independently requires workspace owner/admin permission.

Pilot participants do not see search scores, result rank numbers, attribution identities or percentages, signal breakdowns, contribution analytics, or health scores. Normal non-pilot builds retain the existing extraction and score displays.

This is UI blinding for the bounded pilot, not a new backend authorization boundary. Existing backend workspace permissions remain authoritative, and the isolated pilot protocol must still prevent participants from using direct API tooling or inspecting response payloads before ratings are frozen.

## Operator workflow

1. Sign in as an owner or admin and open `/pilot` directly. The page verifies the current workspace membership before rendering controls.
2. Record each rating with the search event ID, memory ID, rating source (`independent`, `self`, or `external`), the applicable workspace rater or external pseudonym, and exactly one allocation or exclusion reason.
3. Use **Download pilot export** to call the admin-only workspace export endpoint and save its JSON. Participants have no export control.

The operator page does not display candidate names, attribution scores, percentages, ranks, or signal values. Backend permission, workspace-membership, event/result membership, source classification, and idempotency validation remain authoritative.
