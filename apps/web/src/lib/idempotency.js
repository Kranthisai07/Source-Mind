/**
 * Idempotency keys for mutating requests.
 *
 * Shared rather than private to the adapter because the RULE spans both: the
 * adapter mints a key when the caller has none, and a caller retrying the same
 * logical submission must reuse the key it already sent. A duplicate ingestion
 * is not a harmless retry — it creates a second job over the same content.
 *
 * `require_idempotency_key` parses the header with `uuid.UUID(key, version=4)`,
 * so both paths below set the version and variant bits.
 */

/** A UUID v4, using crypto where available. */
export function newIdempotencyKey() {
    const c = typeof crypto !== "undefined" ? crypto : undefined;
    if (c && typeof c.randomUUID === "function") return c.randomUUID();
    // randomUUID is absent on insecure origins and in jsdom.
    const b = new Uint8Array(16);
    if (c && typeof c.getRandomValues === "function") c.getRandomValues(b);
    else for (let i = 0; i < 16; i++) b[i] = Math.floor(Math.random() * 256);
    b[6] = (b[6] & 0x0f) | 0x40;   // version 4
    b[8] = (b[8] & 0x3f) | 0x80;   // variant 10x
    const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
