/**
 * The mock must not lie either: a mock submission produces a mock memory made
 * of the submitted text, and identical text returns the original job flagged
 * as already existing.
 */
import { mockApi } from "../lib/mockApi";

const finish = async (jobId) => {
    mockApi._jobStart[jobId] = Date.now() - 10 * 60 * 1000;
    return mockApi.getJobStatus(jobId);
};

describe("mock ingestion", () => {
    test("success links to a memory holding the submitted text, found by search", async () => {
        const text = `mock submission ${Date.now()} zebra-quartz`;
        const created = await mockApi.createMemory({ content: `  ${text}  ` });
        expect(created.already_exists).toBe(false);

        const job = await finish(created.job_id);
        expect(job.status).toBe("done");
        expect(job.memory_ids).toHaveLength(1);
        expect(job.memory_count).toBe(1);

        const memory = await mockApi.getMemory(job.memory_ids[0]);
        expect(memory.content).toBe(text);
        const found = await mockApi.searchMemories({ query: "zebra-quartz" });
        expect(found.results.some((r) => r.memory_id === job.memory_ids[0])).toBe(true);
    });

    test("identical text returns the original job with already_exists", async () => {
        const text = `duplicate probe ${Date.now()}`;
        const first = await mockApi.createMemory({ content: text });
        const again = await mockApi.createMemory({ content: `${text}\n` });

        expect(again.job_id).toBe(first.job_id);
        expect(again.already_exists).toBe(true);
        const a = await finish(first.job_id);
        const b = await finish(again.job_id);
        expect(b.memory_ids).toEqual(a.memory_ids);
    });

    test("an unknown job never reports a seeded memory as created", async () => {
        const job = await finish("job_unknown_x");
        expect(job.memory_ids).toEqual([]);
    });
});
