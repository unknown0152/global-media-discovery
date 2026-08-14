# Release process

GitHub Releases are built from annotated version tags by
`.github/workflows/release.yml`. Do not upload an untested working tree or
manually replace an artifact produced for an existing tag.

## Prepare

1. Update `VERSION`, `src/gmd/__init__.py`, `pyproject.toml`, `package.json`,
   `.env.example`, Compose fallbacks, frontend asset versions, and the service
   worker cache key.
2. Add a dated `CHANGELOG.md` entry and `docs/releases/vX.Y.Z.md` release notes.
3. Rebuild browser assets and run the complete gates:

   ```bash
   npm ci
   npm run build
   make test
   make release
   ```

4. Verify the generated checksums locally:

   ```bash
   cd ../release
   sha256sum -c global-media-discovery-SHA256SUMS-X.Y.Z.txt
   ```

## Publish

Merge or push the release commit to `main`, confirm CI succeeds, then create and
push an annotated tag:

```bash
git tag -a vX.Y.Z -m "Global Media Discovery X.Y.Z"
git push origin vX.Y.Z
```

The release workflow verifies that the tag matches `VERSION`, reruns all gates,
builds the installer/source/manifest/checksum artifacts from the tagged commit,
and creates the GitHub Release using the versioned notes file.

## Verify

```bash
gh run list --workflow release.yml --limit 1
gh release view vX.Y.Z
gh release download vX.Y.Z --dir /tmp/gmd-release-X.Y.Z
cd /tmp/gmd-release-X.Y.Z
sha256sum -c global-media-discovery-SHA256SUMS-X.Y.Z.txt
```

Confirm the release is marked latest, all four expected assets are present,
the installer is executable after download, the README's latest-release link
resolves, and the live deployment remains healthy.
