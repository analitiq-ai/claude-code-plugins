# Analitiq Pipeline Builder Plugin

A Claude Code plugin for **creating and editing data pipelines and streams**
between [Analitiq DIP](https://github.com/orgs/analitiq-dip-registry/repositories)
connectors. Describe a source and a destination in plain language; the plugin
downloads the connectors from the registry, interviews you for the details, and
authors validated **pipeline**, **stream**, **connection**, and
**database-endpoint** JSON documents against the published schema contract at
[`schemas.analitiq.ai`](https://schemas.analitiq.ai).

It is a **local authoring tool**: it never creates connectors — that's the
[analitiq-connector-builder plugin](https://github.com/analitiq-ai/claude-code-plugins) —
and it calls no registration APIs. It only writes JSON to disk for you to review
and submit.

## Install

```
/plugin marketplace add analitiq-ai/claude-code-plugins
/plugin install analitiq-pipeline-builder@analitiq-claude-code-plugins
```

See the [repository README](../../README.md) for the marketplace, the sibling
connector-builder plugin, and how to develop against a local checkout.

## Use

Launch Claude Code in your project and describe the pipeline you want:

> build a pipeline from Stripe to Snowflake

The plugin then:

1. **Interviews you** — replication method, write mode, schedule, naming.
2. **Downloads** the source and destination connectors from the DIP registry
   (read-only; reused if already on disk).
3. **Authors** a connection per side (with a `.secrets/credentials.json`
   template you fill in), the endpoint documents, the pipeline shell, and one
   stream per endpoint.
4. **Validates** every artifact against the published contract (the offline
   `analitiq-validator` package), and the assembled pipeline through the bundle
   pass (`--bundle-root`).
5. **Writes files** to disk — only once everything passes.

You can also **edit** an existing pipeline in place — e.g. "change the schedule
to hourly" or "add a stream for the customers table". The plugin changes only
what you ask and re-validates; it never regenerates or overwrites your secrets.

Output lands under `connections/`, `pipelines/`, and (read-only) `connectors/`.
Fill in the `.secrets/` templates, then submit the connections and pipeline to
the registry. The full file layout and identity model are documented in
[identity-and-versioning.md](skills/pipeline-builder/references/identity-and-versioning.md);
the secrets workflow in
[spec-envelope.md](skills/connection-spec/spec-envelope.md).

## Validate manually

Validation runs the published, offline `analitiq-validator` package through a
thin adapter (the plugin self-installs it on first use):

```bash
python3 plugins/analitiq-pipeline-builder/scripts/validate.py \
  --entity pipeline \
  --document path/to/pipeline.json \
  --bundle-root path/to/project
```

Output is a single `Diagnostics` JSON object; exit `0` iff `passed: true`. This
plugin's suite lives at the repo root under `tests/pipeline_builder/`. From the
repo root: `pip install -r requirements-dev.txt`, then
`pytest tests/pipeline_builder/`.

The `--entity` values are listed in
[pipeline-schema-validator.md](agents/pipeline-schema-validator.md); how each one
routes is in `scripts/validate.py`'s module docstring.

## How it fits together

This plugin **wires connectors into pipelines** — it does not build the
connectors themselves.

| | Repository | Role |
|---|---|---|
| **This plugin** | [claude-code-plugins](https://github.com/analitiq-ai/claude-code-plugins) | Authors pipelines, streams, connections, and endpoints. |
| Connectors | [analitiq-dip-registry](https://github.com/orgs/analitiq-dip-registry/repositories) | One repository per connector; downloaded read-only. |
| analitiq-connector-builder | [claude-code-plugins](https://github.com/analitiq-ai/claude-code-plugins) | Authors the connectors this plugin consumes. |
| Schemas | [schemas.analitiq.ai](https://schemas.analitiq.ai) | The published JSON Schema contract everything validates against. |

Architecture, the agent chain, and internals are documented in
[CLAUDE.md](CLAUDE.md).

## License

Apache 2.0 — see [LICENSE](../../LICENSE).
