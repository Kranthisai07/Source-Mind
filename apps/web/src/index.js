import React from "react";
import ReactDOM from "react-dom/client";
import { ClerkProvider } from "@clerk/clerk-react";
import "@/index.css";
import App from "@/App";
import { appUrl } from "@/lib/appUrl";

// Publishable keys are designed to ship in the browser bundle — they identify
// the Clerk instance and carry no authority. The secret key stays server-side
// in apps/api/.env and must never appear here.
const CLERK_PUBLISHABLE_KEY = process.env.REACT_APP_CLERK_PUBLISHABLE_KEY;

// Fail loudly rather than rendering a signed-out shell that 401s on every
// call. The same reasoning as the backend's _clerk_jwks_url: a silent fallback
// that can never succeed costs more than an immediate, named error.
if (!CLERK_PUBLISHABLE_KEY) {
    throw new Error(
        "REACT_APP_CLERK_PUBLISHABLE_KEY is not set. Add it to apps/web/.env " +
        "(the pk_test_… value from apps/api/.env) and restart the dev server — " +
        "Create React App only reads .env at startup."
    );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    {/* afterSignOutUrl goes to the browser as an origin-relative path, so it
        needs the mount point. "/" alone would leave the app entirely. */}
    <ClerkProvider publishableKey={CLERK_PUBLISHABLE_KEY} afterSignOutUrl={appUrl("/")}>
      <App />
    </ClerkProvider>
  </React.StrictMode>,
);
