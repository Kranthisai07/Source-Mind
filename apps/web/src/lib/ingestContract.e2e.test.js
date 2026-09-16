/**
 * createMemory against the REAL FastAPI application.
 *
 * Why this exists alongside apiContract.test.js: that file MOCKS `fetch`. It
 * pins the request shape the adapter produces, which is exactly what was wrong
 * — but a mock will happily accept a request the real route rejects, because
 * the mock has no opinion about query parameters or headers. The defect it
 * caught was only a defect *relative to the route's signature*, so the claim
 * "the adapter now matches the route" is only worth anything if the route
 * itself answers.
 *
 * So this submits through `realApi.createMemory` and asserts the API's actual
 * response.
 *
 * WHAT THIS PROVES
 *   The submission is ACCEPTED (HTTP 202) and a job id comes back. That is the
 *   whole of the contract this test covers.
 *
 * WHAT IT DOES NOT PROVE
 *   That ingestion COMPLETED. Completion requires a Celery worker consuming
 *   the queue, and no worker runs here. The job is created and queued; what
 *   happens to it afterwards is not observed, and no claim is made about it.
 *   Anything asserting "the memory exists" would need the worker and is out of
 *   scope for this check.
 *
 * Skipped unless INGEST_API_URL is set, so it never runs in CI or an ordinary
 * `npm test`.
 */

import http from "http";

import { realApi, resetIdentityScopedCaches } from "./realApi";
import { setTokenGetter } from "./authToken";

const API = process.env.INGEST_API_URL;
const describeE2E = API ? describe : describe.skip;

/** Minimal fetch over Node's http — jsdom ships none. */
function nodeFetch(url, init = {}) {
    return new Promise((resolve, reject) => {
        const u = new URL(String(url));
        const req = http.request(
            {
                hostname: u.hostname,
                port: u.port,
                path: u.pathname + u.search,
                method: init.method || "GET",
                headers: init.headers || {},
            },
            (res) => {
                let body = "";
                res.setEncoding("utf8");
                res.on("data", (d) => { body += d; });
                res.on("end", () =>
                    resolve({
                        ok: res.statusCode >= 200 && res.statusCode < 300,
                        status: res.statusCode,
                        statusText: res.statusMessage || "",
                        headers: { get: (k) => res.headers[String(k).toLowerCase()] ?? null },
                        text: async () => body,
                    })
                );
            }
        );
        req.on("error", reject);
        if (init.body) req.write(init.body);
        req.end();
    });
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

describeE2E("createMemory against the real API", () => {
    jest.setTimeout(30000);

    beforeAll(() => {
        global.fetch = nodeFetch;
        // No Clerk here; the API runs under its validated development bypass.
        setTokenGetter(null);
    });

    afterAll(() => {
        delete global.fetch;
        resetIdentityScopedCaches();
    });

    test("a submission is accepted and returns a job id", async () => {
        const result = await realApi.createMemory({
            content: "Vector index is HNSW with m=16 — submitted through the adapter.",
            tags: ["contract"],
            category: "general",
        });

        // 202 Accepted with IngestionJobResponse. If the adapter still sent
        // workspace_id in the body, or omitted the Idempotency-Key header, this
        // would have rejected before the handler ran and realApi would have
        // thrown instead of resolving.
        expect(result).toBeTruthy();
        expect(result.job_id).toMatch(UUID);
    });

    test("two separate submissions get two different job ids", async () => {
        // Separate submissions mean separate keys, so they must not collapse
        // into one job.
        const a = await realApi.createMemory({ content: "first distinct submission" });
        const b = await realApi.createMemory({ content: "second distinct submission" });

        expect(a.job_id).toMatch(UUID);
        expect(b.job_id).toMatch(UUID);
        expect(a.job_id).not.toBe(b.job_id);
    });

    test("a retry reusing the original key is accepted", async () => {
        // The retry contract: the same logical submission, resent with the key
        // the first attempt used. The API must accept it rather than reject
        // the header.
        const key = "7c9e6679-7425-40de-944b-e07fc1f90ae7";
        const content = "a submission that gets retried";

        const first = await realApi.createMemory({ content, idempotencyKey: key });
        const retry = await realApi.createMemory({ content, idempotencyKey: key });

        expect(first.job_id).toMatch(UUID);
        expect(retry.job_id).toMatch(UUID);

        // Deliberately NOT asserting the two job ids are equal. Whether the
        // backend deduplicates on the key is its behaviour to define, and
        // asserting it here would be asserting a contract nobody has stated.
        // What matters for the client is that a correctly-keyed retry is
        // accepted rather than rejected.
    });

    test("a malformed key is rejected — the header is really being read", async () => {
        // Proves the API is validating the header this adapter sends, rather
        // than the tests above passing for some unrelated reason.
        await expect(
            realApi.createMemory({ content: "bad key", idempotencyKey: "not-a-uuid" })
        ).rejects.toMatchObject({ status: 400 });
    });
});
