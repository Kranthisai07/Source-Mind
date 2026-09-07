/**
 * Contract test: every AttributionActionType value has an activity colour.
 *
 * The original ACTION_COLOR map was written against mockData and shared no key
 * at all with the API, so every real event rendered in the default blue. That
 * is invisible in a screenshot and impossible to catch from an HTTP status, so
 * it is pinned here instead.
 *
 * The enum is PARSED FROM THE BACKEND SOURCE rather than duplicated. A copied
 * list would only restate what the author believed on the day; reading
 * attribution.py means adding a sixth member to the enum fails this test until
 * the colour map is updated too.
 */

const fs = require("fs");
const path = require("path");

const { ACTION_COLOR } = require("./realApi");

const ENUM_SOURCE = path.resolve(
    __dirname,
    "../../../api/sourcemind/models/attribution.py"
);

/** Members of `class AttributionActionType(StrEnum)` as their string values. */
function backendActionTypes() {
    const src = fs.readFileSync(ENUM_SOURCE, "utf8");
    const cls = src.match(
        /class AttributionActionType\(StrEnum\):([\s\S]*?)(?=\nclass |\n\n\n)/
    );
    if (!cls) throw new Error(`AttributionActionType not found in ${ENUM_SOURCE}`);
    return [...cls[1].matchAll(/^\s{4}[A-Z_]+\s*=\s*"([a-z_]+)"/gm)].map((m) => m[1]);
}

// Mock-only keys, kept so the offline demo still renders in colour. Listed
// explicitly so the dead-key check below stays meaningful.
const MOCK_ONLY = [
    "memory_created",
    "memory_edited",
    "conflict_opened",
    "conflict_resolved",
    "connector_synced",
    "handoff_started",
];

describe("ACTION_COLOR vs AttributionActionType", () => {
    test("the backend enum is readable and non-empty", () => {
        const values = backendActionTypes();
        expect(values.length).toBeGreaterThan(0);
        // Guards against the regex silently matching nothing after a refactor.
        expect(values).toContain("create");
    });

    test("every backend action value has a colour", () => {
        const missing = backendActionTypes().filter((v) => !(v in ACTION_COLOR));
        expect(missing).toEqual([]);
    });

    test("every colour is a hex value", () => {
        for (const [key, value] of Object.entries(ACTION_COLOR)) {
            expect(`${key}=${value}`).toMatch(/=#[0-9A-Fa-f]{6}$/);
        }
    });

    test("no dead keys: every entry is a backend value or a declared mock key", () => {
        const allowed = new Set([...backendActionTypes(), ...MOCK_ONLY]);
        const dead = Object.keys(ACTION_COLOR).filter((k) => !allowed.has(k));
        // conflict_detected and handoff_initiated used to live here and matched
        // neither the API nor mockData.
        expect(dead).toEqual([]);
    });

    test("mock keys still resolve, so REACT_APP_USE_MOCKS=true keeps its colours", () => {
        const missing = MOCK_ONLY.filter((k) => !(k in ACTION_COLOR));
        expect(missing).toEqual([]);
    });
});
