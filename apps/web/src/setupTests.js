/**
 * Test environment shims.
 *
 * react-scripts 5 pins a jsdom old enough to predate TextEncoder/TextDecoder,
 * which react-router 7 uses at module scope. Without these, importing anything
 * that reaches the router throws ReferenceError before a single test runs.
 *
 * Both are standard Node globals; this only puts them where jsdom left a hole.
 */

import { TextDecoder, TextEncoder } from "util";

if (typeof global.TextEncoder === "undefined") global.TextEncoder = TextEncoder;
if (typeof global.TextDecoder === "undefined") global.TextDecoder = TextDecoder;
