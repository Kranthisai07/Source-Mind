// Absolute, origin-relative URLs that respect the router's basename.
//
// The app is mounted under a sub-path. package.json sets
//   "homepage": "https://Kranthisai07.github.io/Source-Mind"
// and Create React App turns that into PUBLIC_URL="/Source-Mind", which
// App.js passes to <BrowserRouter basename={...}>. The dev server serves the
// app at that same sub-path, so it is "/Source-Mind" locally too, not just on
// GitHub Pages.
//
// React Router's own <Link to="/x"> and <Navigate to="/x"> are basename-aware
// and must NOT use this helper - they would end up with the prefix twice.
//
// Clerk is the opposite case. Props like forceRedirectUrl and afterSignOutUrl
// are handed to the browser as origin-relative paths; Clerk knows nothing
// about React Router. "/dashboard" therefore navigates to
// http://localhost:3000/dashboard, which falls outside the basename, so the
// router matches nothing at all - not even the catch-all route, since that is
// itself scoped inside the basename. The result is a blank page after a
// successful sign-in.

const BASENAME = (process.env.PUBLIC_URL || "").replace(/\/+$/, "");

/**
 * Prefix an in-app path with the mount point.
 *
 *   appUrl("/dashboard")  ->  "/Source-Mind/dashboard"   (PUBLIC_URL set)
 *   appUrl("/dashboard")  ->  "/dashboard"               (PUBLIC_URL empty)
 */
export function appUrl(path = "/") {
    const suffix = path.startsWith("/") ? path : `/${path}`;
    return `${BASENAME}${suffix}`;
}

export { BASENAME };
