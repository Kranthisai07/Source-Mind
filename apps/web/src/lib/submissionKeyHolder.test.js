/**
 * The key holder's own rules.
 *
 * Separate from the component tests because one property cannot be expressed
 * through the UI at all: after a SUCCESSFUL submission the panel switches to
 * job tracking, so the only route to a second submission is a reset — which
 * clears the holder regardless. At component level "success cleared the key"
 * and "reset cleared the key" are indistinguishable, so asserting it there
 * would prove nothing.
 */

import { createSubmissionKeyHolder, submissionIdentity } from "./submissionKey";

const identity = (over = {}) =>
    submissionIdentity({
        payload: { content: "c", tags: [], category: "general" },
        workspaceId: "ws-1",
        userId: "user-a",
        ...over,
    });

describe("submission key holder", () => {
    test("the same identity keeps the same key", () => {
        const h = createSubmissionKeyHolder();
        const a = h.keyFor(identity());
        expect(h.keyFor(identity())).toBe(a);
    });

    test.each([
        ["content", { payload: { content: "different", tags: [], category: "general" } }],
        ["tags", { payload: { content: "c", tags: ["x"], category: "general" } }],
        ["category", { payload: { content: "c", tags: [], category: "decision" } }],
        ["workspace", { workspaceId: "ws-2" }],
        ["user", { userId: "user-b" }],
    ])("a changed %s earns a new key", (_label, over) => {
        const h = createSubmissionKeyHolder();
        const first = h.keyFor(identity());
        expect(h.keyFor(identity(over))).not.toBe(first);
    });

    test("clear() means the next identical submission is a NEW one", () => {
        // Called once a submission is accepted. Without it, a later identical
        // submission would be sent as a replay of the accepted one.
        const h = createSubmissionKeyHolder();
        const first = h.keyFor(identity());
        h.clear();
        expect(h.keyFor(identity())).not.toBe(first);
    });

    test("an unknown user is part of the identity, not ignored", () => {
        // A key minted while the bridge had not yet published a user must not
        // be reused once a real id arrives.
        const h = createSubmissionKeyHolder();
        const anon = h.keyFor(identity({ userId: null }));
        expect(h.keyFor(identity({ userId: "user-a" }))).not.toBe(anon);
    });
});
