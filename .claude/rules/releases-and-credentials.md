# Rule: releases and credentials

Governs every change to a release workflow, a package version, a `plugin.json`, or
the credentials a publish uses.

Each publishable artifact has its own tag prefix. The plugins are
release-please-managed — never bump a `plugin.json` version by hand. The packages are
released by hand as ONE PR, merged with a merge commit, never a squash. Publishing to
PyPI and the schemas bucket is OIDC only — never add a static credential for either as
a repo or environment secret (the `pypi` / `schemas` environments), and never use
`pull_request_target` with a checkout of PR code.

Full procedure, commit-type rules and the `pypi` / `schemas` environment settings live
in the `releasing` skill (Claude Code tooling under an ignored directory, so it ships
with the maintainer's checkout and not with a clone). If you have it, invoke it by
name; if you do not, the environment settings are on the GitHub settings pages and the
rest is the rules above.
