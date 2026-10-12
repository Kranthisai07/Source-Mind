# Publishing the web frontend to GitHub Pages

The frontend (`apps/web`) is a static Create React App build served by GitHub Pages from the `gh-pages` branch
(`https://kranthisai07.github.io/Source-Mind/`, Pages source: branch `gh-pages`, path `/`). Publishing is a manual release step: nothing
publishes on a push to `main`. This page is the procedure; the reasoning is in `docs/architecture/DECISIONS.md` (D-028).

Publish the frontend LAST in a release, after the API and worker already run the matching commit, because the new bundle calls the new API contracts.

## What a published branch looks like

`gh-pages` holds only compiled output: `index.html`, `404.html`, `asset-manifest.json`, `static/js/main.<hash>.js` (+ `.LICENSE.txt`, `.map`),
`static/css/main.<hash>.css` (+ `.map`) and `images/`. Earlier releases replaced the branch with one parentless commit per publish, with a
message of the form `Publish <sha12> (main, PR #<n>): <summary>`. This procedure keeps that shape but pushes it with a lease so it can never overwrite a
head you did not expect.

## 1. Preconditions

* The release commit `<RELEASE_SHA>` is on `main` and CI is green on that exact SHA.
* A clean checkout of exactly that commit: `git worktree add --detach <WORKTREE> <RELEASE_SHA>`, then `git -C <WORKTREE> rev-parse HEAD` equals `<RELEASE_SHA>` and `git status --porcelain` is empty.
* Node 22 (the version CI uses) and npm.
* The Clerk **publishable** key of the same Clerk instance the API uses (a public value, `pk_test_...` for a development instance). Pass it through the environment; do not commit it or print it into logs.
* Record the current `gh-pages` head and keep a local copy of it for rollback:
  ```
  PREV=$(git ls-remote origin refs/heads/gh-pages | cut -f1); echo "$PREV"
  git fetch origin gh-pages && git tag backup/gh-pages-${PREV:0:7} "$PREV"
  ```

## 2. Build

```
cd <WORKTREE>/apps/web
npm ci --no-audit --no-fund          # .npmrc sets legacy-peer-deps=true; the lockfile needs it
export REACT_APP_USE_MOCKS=false     # unset means mock mode (src/lib/api.js)
export REACT_APP_BACKEND_URL=<API public URL, no trailing slash>
export REACT_APP_CLERK_PUBLISHABLE_KEY=<publishable key>
export REACT_APP_PILOT_MODE=false    # research pilot tools stay off for ordinary use
unset REACT_APP_DEFAULT_WORKSPACE_ID # the app resolves the signed-in user's own workspace list
CI=false npm run build               # craco build, then scripts/copy-404.js writes build/404.html
```

`CI=false` is required because Create React App otherwise treats warnings as errors (CI does the same). `404.html` is a copy of the built `index.html`: GitHub Pages serves it for any
unknown path while keeping the browser URL, which is what makes deep links such as `/Source-Mind/dashboard` work.

Check the build before publishing. The bundle must have mock mode off, pilot mode off, the expected API URL, a publishable-key literal and no secret-key literal, the `/Source-Mind` public path,
and a `404.html` identical to `index.html`:

```
grep -c 'REACT_APP_USE_MOCKS:"false"'  build/static/js/main.*.js
grep -c 'REACT_APP_PILOT_MODE:"false"' build/static/js/main.*.js
grep -c 'REACT_APP_BACKEND_URL:"<API public URL>"' build/static/js/main.*.js
grep -c 'PUBLIC_URL:"/Source-Mind"'    build/static/js/main.*.js
grep -E -c 'sk_(test|live)_[A-Za-z0-9]{10}' build/static/js/main.*.js build/index.html   # must be 0
cmp build/index.html build/404.html
```

Each of the first four must print a count of at least 1, the `sk_` count must be 0, and `cmp` must print nothing. (Release operators use a scripted version of these checks.)

## 3. Publish

```
SHA12=${RELEASE_SHA:0:12}
STAGE=$(mktemp -d)
cp -a build/. "$STAGE"/
cd "$STAGE"
git init -q -b gh-pages
git -c core.autocrlf=false add -A
git -c core.autocrlf=false commit -q -m "Publish $SHA12 (main, PR #<n>): <summary>"
git ls-tree -r --name-only HEAD | grep -E '^(index\.html|404\.html|static/js/main\..*\.js)$'   # all three present
git push --force-with-lease=refs/heads/gh-pages:$PREV "$(git -C <WORKTREE> remote get-url origin)" gh-pages:refs/heads/gh-pages
```

Use your own git identity and no attribution lines in the message. `--force-with-lease=refs/heads/gh-pages:$PREV` makes the push fail with `(stale info)` if `gh-pages` is not at the head you recorded, so a
publish by someone else is never overwritten. Never use a plain `--force`. (`gh-pages -d build --no-history`, which earlier releases appear to have used, force-pushes without that check. `npm run deploy` without options
appends a commit instead and needs no force at all; either is acceptable, the lease form is the default here. The exact command used by earlier releases is not recorded.)

## 4. Verify the published site

1. Pages build: `gh api repos/<owner>/<repo>/pages/builds/latest --jq '{status,commit}'` until `status` is `built` and `commit` equals the new head (builds have taken about 40 seconds).
2. Download `index.html`, `404.html` and the `main.<hash>.js` named in it from the live site and repeat the greps of section 2 on the downloaded files. The hash must be the one you built.
3. `https://kranthisai07.github.io/Source-Mind/dashboard` answers with the app (Pages returns it with status 404, which is expected for the fallback), and the body equals `index.html`.
4. In a private window, sign in; the browser console shows `[SourceMind] API mode: REAL`.

## 5. Roll back

The site is static; a rollback does not touch the API or the data. Restore the recorded head with a lease on the new head:

```
git push --force-with-lease=refs/heads/gh-pages:<NEW_SHA> origin $PREV:refs/heads/gh-pages
```

`$PREV` must exist locally (section 1 tags it). Then repeat the Pages build check.

## 6. Caching

GitHub Pages sends `Cache-Control: max-age=600`. After a publish, `index.html` may be served from cache for up to ten minutes and then point at the previous hashed bundle, which no longer exists
once the new tree is live. Hard-reload (Ctrl+Shift+R) and allow ten minutes before judging a result. Hashed asset names mean a fully reloaded page always gets a consistent pair.
