// API dispatcher: picks between mock client and real backend at runtime.
//
// Default is mock mode so the demo keeps working even without a backend.
// Set REACT_APP_USE_MOCKS=false in frontend/.env to hit the real backend.
//
// Both clients return identical shapes — UI components need no changes.

import { mockApi } from "./mockApi";
import { realApi } from "./realApi";

const useMocks = (process.env.REACT_APP_USE_MOCKS ?? "true").toLowerCase() !== "false";

// eslint-disable-next-line no-console
console.info(
    `[SourceMind] API mode: ${useMocks ? "MOCK" : "REAL"}` +
    (useMocks ? "" : ` · base=${process.env.REACT_APP_BACKEND_URL}`)
);

export const api = useMocks ? mockApi : realApi;
export default api;
export { useMocks };
