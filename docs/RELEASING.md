# Releasing

A release is a tag. `release.yml` runs the tests, builds the wheel, creates the GitHub release from the matching
`CHANGELOG.md` section and, once enabled, publishes to PyPI.

## Every release
1. Set the version in `pyproject.toml` and `src/specanvil/__init__.py`.
2. Add a `## <version>` section at the top of `CHANGELOG.md`.
3. Merge to `main` and wait for CI to pass.
4. `git tag v<version> && git push origin v<version>`

## One-time: publish to PyPI
Publishing uses PyPI trusted publishing, so no token is stored in the repository.

1. Sign in at pypi.org and open **Publishing** in your account settings.
2. Add a pending publisher: project name `specanvil`, owner `Abdulwahab26`, repository `SpecAnvil`,
   workflow `release.yml`, environment left empty.
3. In the GitHub repository: Settings > Secrets and variables > Actions > Variables, add `PYPI_PUBLISH` = `true`.
4. Push the next tag. Check the result with `pipx install specanvil && specanvil doctor`.
5. In `README.md`, replace the `pipx install git+https://...` line with `pipx install specanvil`.

The name `specanvil` was free on PyPI when 0.9.0 was written. Check again before step 2.

## One-time: make the repository public
The repository was renamed from `Specforge` to `SpecAnvil` on 2026-11-12. GitHub redirects the old URL, so existing
clones and `pip install git+https://github.com/Abdulwahab26/Specforge` keep working.

1. Before Settings > General > Change visibility > Public: read through the git history for anything that should not
   be public (keys, private repo names, customer data). History becomes public with the code.
2. After it is public: add topics (`mcp`, `claude-code`, `spec-driven-development`), enable private vulnerability
   reporting (Settings > Security), and turn on Dependabot alerts.
