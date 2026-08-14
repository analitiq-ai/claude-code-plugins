# Analitiq Claude Code Plugins

A Claude Code **plugin marketplace** holding the plugins that author Analitiq
data-integration artifacts, plus (as they land) the Python packages and JSON
Schemas that define what those artifacts must look like.

These are one contract surface, not independent projects sharing a directory.
The plugin prose, the contract models, the validator, and the published schemas
are four expressions of one set of rules — they live together so a rule changes
in one place.

## Install

Inside Claude Code:

```
/plugin marketplace add analitiq-ai/claude-code-plugins
/plugin install analitiq-connector-builder@analitiq-claude-code-plugins
/plugin install analitiq-pipeline-builder@analitiq-claude-code-plugins
```

The marketplace is named **`analitiq-claude-code-plugins`** — that is the name
after the `@`. It comes from `.claude-plugin/marketplace.json` and is independent
of the repository name.

Pull later updates with:

```
/plugin marketplace update analitiq-claude-code-plugins
```

To develop against a local checkout instead of GitHub:

```
/plugin marketplace add /path/to/claude-code-plugins
```

## Plugins

| Plugin | Authors | Docs |
|---|---|---|
| **analitiq-connector-builder** | Connector + API endpoint documents; for database connectors, the installable Python package too | [README](plugins/analitiq-connector-builder/README.md) |
| **analitiq-pipeline-builder** | Pipeline, stream, connection, and database-endpoint documents, wiring registry connectors together | [README](plugins/analitiq-pipeline-builder/README.md) |

They compose: connector-builder produces connectors published to the
[DIP registry](https://github.com/orgs/analitiq-dip-registry/repositories);
pipeline-builder downloads them and wires them into pipelines. Neither authors
the other's documents.

## Layout

```
.claude-plugin/marketplace.json   # marketplace catalog; one entry per plugin
plugins/<name>/                   # the distribution artifact — copied to users verbatim
packages/contract-models/         # → analitiq-contract-models on PyPI — the contract itself
packages/validator/               # → analitiq-validator on PyPI
schemas/                          # rendered public JSON Schemas → schemas.analitiq.ai
scripts/render_schemas.py         # renders schemas/ from packages/contract-models
tests/<plugin>/                   # one suite per plugin (package suites live beside their package)
```

## Development

```bash
pip install -r requirements-dev.txt
git config core.hooksPath .githooks   # once per clone — see below
pytest
```

Much of this repo is rendered output — the compiled rule registry, each
plugin's rule reference, the pipeline plugin's generated prose blocks, the
prose census, the Contents sections. `python3 scripts/render_all.py write`
regenerates all of it in dependency order, and `check` verifies it (CI runs
that). The tracked pre-commit hook in `.githooks/` runs `write` for you
whenever a commit stages a file a generator reads, and aborts the commit if
regeneration changed files it does not carry — hence the `core.hooksPath`
line above. Sessions in Claude Code get the same regeneration from the
project hook in `.claude/settings.json` after any edit to a rule record or a
contract package.

Contributor guidance lives in `CLAUDE.md` (repo-wide) and
`contributing/<plugin-name>.md` (per plugin);
[CONTRIBUTING.md](CONTRIBUTING.md) carries the filing and closing discipline —
read it before opening or closing an issue.

Releases are handled by **release-please** in monorepo mode — each plugin has its
own version, changelog, and `<component>-vX.Y.Z` tag. Never bump a `plugin.json`
version by hand; the bump is derived from Conventional Commit types, so scope
your PR title to the artifact (`feat(connector-builder): …`).

## Links

- [Published schemas](https://schemas.analitiq.ai) — the authoritative JSON Schemas everything validates against.
- [DIP registry](https://github.com/orgs/analitiq-dip-registry/repositories) — one repo per published connector.

## License

Apache 2.0 — see [LICENSE](LICENSE).
