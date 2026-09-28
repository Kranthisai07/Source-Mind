#!/usr/bin/env node
// GitHub Pages serves this repo's 404.html for any request with no matching
// file, keeping the browser's original URL (path, query string, fragment)
// unchanged — it is content substitution on a 404 response, not a redirect.
//
// This build's assets are referenced by ABSOLUTE path (/Source-Mind/static/...),
// and BrowserRouter reads location.pathname/search/hash directly at mount, so
// serving the already-built index.html verbatim as 404.html is sufficient: no
// redirect-encode/decode script is needed. Verified locally against a server
// that reproduces GitHub Pages' actual 404-content-on-original-URL behavior —
// deep links, including ones carrying a query string and a fragment, resolve
// to the correct route with the URL fully intact.
//
// Must run AFTER the build (as a postbuild step), not from public/: the
// public/ copy of index.html is an unprocessed template (%PUBLIC_URL%
// placeholders, no injected script tags). Only the built build/index.html has
// gone through CRA's HtmlWebpackPlugin processing.
const fs = require("fs");
const path = require("path");

const buildDir = path.join(__dirname, "..", "build");
const source = path.join(buildDir, "index.html");
const target = path.join(buildDir, "404.html");

if (!fs.existsSync(source)) {
    console.error(`copy-404: ${source} does not exist — run the build first.`);
    process.exit(1);
}
fs.copyFileSync(source, target);
console.log(`copy-404: wrote ${target} from the built index.html`);
