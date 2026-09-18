# Releasing MikroMan

The pipeline is: bump → merge → tag + publish → multi-arch images → the update
notice reaches running deployments. Every step below carries its own
verification, because a release that skips one is invisible to installations
that are already running.

---

## 1. Bump the version

One version number lives in two places, and both must move together:

| File | What it holds |
|---|---|
| `frontend/package.json` | `version` — compiled into the bundle as `__APP_VERSION__` by `vite.config.js` |
| `backend-go/internal/config/config.go` | the `APP_VERSION` default — what the API reports and what the update check compares |

The same commit carries `frontend/package-lock.json` (its two app-version
entries) and the version fixtures in `frontend/src/components/AppFooter.test.jsx`.
The insertion count grows with the fixture set, so don't verify by a fixed
number — verify that no stale old version survives, and that a transitive
dependency which happens to be released under the same number keeps its own
version: anchor lockfile edits on the package name (`"name":
"mikroman-frontend"`), never on `"version": "<x.y.z>"`, because a dependency can
legitimately be published as the same triple and would be silently corrupted.

```bash
git checkout -b chore/bump-vX.Y.Z
# edit the four files
cd backend-go && go test ./internal/... && cd ..
cd frontend && npm ci && npm test -- --run && npm run build && cd ..
node frontend/scripts/check-identifiers.cjs
```

---

## 2. Merge through a pull request

CI runs the same gate on the pull request (`Validate Code & Build Assets`) and
skips the publishing job there, so a pull request can never push an image.

A change to `.github/workflows/` must be pushed with the deploy key rather than
an API token: a fine-grained personal access token needs the `Workflows`
permission for that and is refused without it (`gh pr update-branch` is refused
for the same reason — merge the base branch locally and push instead).

---

## 3. Tag and publish

```bash
gh release create vX.Y.Z --target "$(git rev-parse origin/master)" \
  --title "vX.Y.Z: <short description>" --notes-file <notes>
```

Before publishing, run the identifier sweep and re-read the notes as an
adversary rather than as their author: the mechanism may be described, never the
installation we happened to live in. Not every release carries user-visible
features (CI, packaging and dependency work do not); say what moved instead of
inventing feature prose.

---

## 4. Verify the release, not the job status

```bash
gh run watch <run-id>                                    # release event: validate + publish
docker manifest inspect ghcr.io/masseselsev/mikroman:X.Y.Z
```

Expect three platforms — `linux/amd64`, `linux/arm64`, `linux/arm/v7` — plus the
tags `X.Y.Z`, `X.Y`, `X` and `latest` (`latest` only moves on a release event).

---

## 5. The release must be visible to running deployments

The in-app notice compares the running build against GitHub's *latest release*,
so a release only reaches installations when all of this holds:

- **Published**, not a draft and not marked prerelease — `/releases/latest` skips
  both, so a prerelease tag must never be the one deployments upgrade to.
- **The tag is `vX.Y.Z` and equals the bumped version** in both files above. The
  comparison is numeric-triple based; a mismatch makes the footer report a
  version that does not exist upstream, or suppresses a real update.
- **`make_latest` stays on for the newest release.** GitHub elects "latest" by
  release *creation time*, not by semver: when back-filling a release for an
  older tag, pass `--latest=false`, otherwise the update check starts comparing
  against the older version.
- **Nothing pins `APP_VERSION`** in a deployment file (compose, environment). The
  image's baked default is the source of truth; an override drifts from the
  released tag and breaks the comparison for that deployment alone.

Freshness after publishing: the backend caches the check for 15 minutes, and the
dashboard re-asks on page load, on tab focus and every 15 minutes, so the footer
badge appears within that window on a page load. `?force=true` on
`GET /api/v1/system/version-check` bypasses the cache for a manual look.