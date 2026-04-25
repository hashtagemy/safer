# Releasing SAFER to PyPI

Both `safer-sdk` and `safer-backend` ship from the same tag at the same
version. The release flow is fully automated by
[`.github/workflows/auto-release.yml`](../.github/workflows/auto-release.yml):
push a tag, the workflow builds both packages, uploads them via the
stored `PYPI_API_TOKEN` secret, and opens a GitHub Release.

## Prerequisites (one-time)

1. PyPI account on https://pypi.org/user/hashtagemy with 2FA enabled.
2. Account-scoped (or both-projects-scoped) PyPI API token saved as
   `PYPI_API_TOKEN` in
   https://github.com/hashtagemy/safer/settings/secrets/actions.

After the very first publish creates `safer-sdk` and `safer-backend`
on PyPI, you can rotate the secret to a project-scoped token covering
just those two projects (recommended for blast-radius).

## Releasing a new version

1. **Bump the version in BOTH pyproject files.** They must match the
   tag exactly — the workflow refuses to publish if `tag != sdk_version`
   or `tag != backend_version`.

   ```
   packages/sdk/pyproject.toml         version = "0.1.1"
   packages/backend/pyproject.toml     version = "0.1.1"
   ```

   For backend, also bump the SDK pin if appropriate:

   ```toml
   "safer-sdk>=0.1.1,<0.2",
   ```

2. **Land the bump on `main`.** Open a small PR, get it merged.

3. **Tag from `main` and push:**

   ```bash
   git checkout main
   git pull
   git tag v0.1.1
   git push origin v0.1.1
   ```

4. **Watch the workflow:**
   https://github.com/hashtagemy/safer/actions

   It does, in order:
   - Sanity-checks the tag matches both pyproject versions
   - `python -m build packages/sdk` → `dist/safer_sdk-0.1.1-*`
   - `python -m build packages/backend` → `dist/safer_backend-0.1.1-*`
   - `twine upload --skip-existing dist/*` against PyPI
   - Creates the GitHub Release with the `pip install` block

5. **Verify on PyPI** (usually visible within a minute):
   - https://pypi.org/project/safer-sdk/0.1.1/
   - https://pypi.org/project/safer-backend/0.1.1/

   And the GitHub Release at
   https://github.com/hashtagemy/safer/releases/tag/v0.1.1.

## Versioning policy

- **Both packages share a version.** They ship as a matched pair —
  `safer-backend` pins `safer-sdk>=0.1.0,<0.2`, so a backend bump that
  expects new SDK behaviour also bumps the SDK.
- Semver:
  - Patch (`0.1.1`) — no API changes; bug fixes, doc updates, internal
    refactors.
  - Minor (`0.2.0`) — additive API changes (new adapter, new tool,
    new dashboard route). Bumps the SDK pin's upper bound for backend.
  - Major (`1.0.0`) — breaking changes. Run a deprecation cycle in
    the prior minor.
- Pre-release tags like `v0.2.0-rc1` work too — the workflow doesn't
  treat them specially, but PyPI marks them as pre-releases that
  `pip install` won't pick up by default.

## Re-running a failed release

`twine upload --skip-existing` makes the workflow idempotent. If the
release fails after building (network, GH release step), re-running
the workflow against the same tag is safe — it will skip artefacts
that already exist on PyPI.

If the release fails BEFORE the upload (the version-check step, say),
fix the bump, force-update the tag, and re-push:

```bash
git tag -f v0.1.1
git push -f origin v0.1.1
```

## Troubleshooting

- **`twine: 403 Forbidden`** → token is missing the project, or 2FA
  isn't on the account. Check
  https://pypi.org/manage/account/token/.
- **`Tag v0.1.1 does not match safer-sdk version`** → forgot to bump
  one of the two pyprojects. Update + re-tag.
- **`safer-sdk>=0.1.0,<0.2` resolves nothing** → the SDK version isn't
  on PyPI yet. Make sure SDK uploads successfully before the backend's
  install resolves. The workflow uploads both in one `twine upload`
  call, which uploads in alphabetical order by default — `safer_backend`
  first, then `safer_sdk`. PyPI accepts both even if the resolver
  order is "wrong" because both are in the same upload.
