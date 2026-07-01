# Releasing

Cutting a release is **push a version tag**. GitHub Actions builds the wheel +
sdist and publishes them to PyPI automatically — there is no token to manage and
no `twine` to run by hand.

## Steps

1. **Bump the version** in two places (keep them identical):
   - `pyproject.toml` → `version`
   - `src/spatial_standards/__init__.py` → `__version__`

   Use [SemVer](https://semver.org): patch (`0.1.2` → `0.1.3`) for fixes, minor
   (`0.1.x` → `0.2.0`) for features. Between releases, `main` carries a
   `X.Y.Z.dev0` version so it isn't sitting on a published number.

2. **Update `CHANGELOG.md`** — turn the top `## vX.Y.Z — unreleased` section into
   the release, or add one.

3. **Commit** the bump + changelog to `main`.

4. **Tag and push** — this is what publishes:

   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z — <one line>"
   git push origin vX.Y.Z
   ```

   `.github/workflows/publish.yml` triggers on `v*` tags, builds, and uploads to
   PyPI via **Trusted Publishing** (OIDC — no stored secret).

5. **Verify** it went live (~2–4 min):

   ```bash
   curl -s https://pypi.org/pypi/natural-perspective-spatial-audio/json \
     | python3 -c "import json,sys; print(json.load(sys.stdin)['info']['version'])"
   ```

   If it doesn't appear, check the **Actions** tab for a failed run.

6. **(Optional) GitHub Release** — Releases → *Draft a new release* → pick the
   tag → paste the changelog → Publish. Nice-to-have, not required for install.

## One-time setup (already done)

PyPI Trusted Publisher, configured at PyPI → project → *Settings → Publishing*:

| Field | Value |
|---|---|
| Owner | `mattluttrell` |
| Repository | `natural-perspective-spatial-audio` |
| Workflow filename | `publish.yml` |
| Environment name | `pypi` |

## If a release is broken

PyPI versions are immutable — you can't overwrite one. **Yank** it instead
(PyPI → project → *Manage → Releases* → version → *Yank*): it stays installable
if explicitly pinned, but pip stops choosing it. Then ship a fixed patch version.

## Notes

- **Supported Python: 3.10+.** The `[full]` extra pulls PyTorch + `torchcodec`
  (its audio I/O links the system FFmpeg the user installs), so a `[full]`
  install works on current Pythons including 3.13/3.14.
- FFmpeg is a **system** dependency (`brew install ffmpeg` / `apt install
  ffmpeg`), not a Python package — see `README.md`.
