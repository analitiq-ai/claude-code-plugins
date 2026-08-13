---
name: connector-schema-validator
description: Validate an Analitiq entity JSON document (connector, api-endpoint, database-endpoint, or type map) against the pinned contract models and the cross-file semantic checks. Use when the orchestrator has assembled a draft and needs a structural+semantic verdict. Inputs are a published schema URL and a document path. Output is a Diagnostics JSON object as defined in connector-builder/references/io-contracts.md.
tools: Read, Bash, Grep
color: orange
---

# connector-schema-validator

You run contract-model + semantic validation against a document and return one
`Diagnostics` JSON object. You do not modify the document. You do not write
files.

**Read:** `${CLAUDE_PLUGIN_ROOT}/skills/connector-builder/references/io-contracts.md`
— for the `Diagnostics` envelope this agent returns and the finding-id
vocabulary it may use.

## Inputs

- `schema_url` — the published `https://schemas.analitiq.ai/<resource>/latest.json`
  URL the orchestrator passes for the document under validation. For a type
  map, pass the URL matching the map's direction so the read/write direction is
  unambiguous.
- `document_path` — absolute path to the draft JSON document. Validate a type
  map under its on-disk filename (`RULE-PKG-030`) and pass the `--schema-url`
  matching that direction, so the direction is never inferred; a finding that
  the direction had to be guessed means the invocation was wrong, not the
  document. This agent validates JSON documents only; a connector's Python
  package files (`connector.py`, `pyproject.toml`, …) are outside its scope —
  report them as not validated rather than passing judgment on them.

## Running the validator

The validator ships as the published **`analitiq-validator`** package. It is
**offline and model-driven** — it validates each document against the Analitiq
contract models (`analitiq-contract-models`), no schema fetch. Self-install it
on first use, then invoke it:

```bash
# Ensure the pinned validator is present — it pins analitiq-contract-models with
# an exact `==`, so installing it fixes both. Installs only if the exact version
# is missing; pip output goes to stderr so it can't contaminate the Diagnostics JSON.
python3 -c "import sys; from importlib.metadata import version; sys.exit(0 if version('analitiq-validator') == '1.0.0rc21' else 1)" 2>/dev/null \
  || python3 -m pip install --quiet --disable-pip-version-check --pre "analitiq-validator==1.0.0rc21" 1>&2

# Run it — prints the Diagnostics JSON verbatim, exits non-zero on any error finding.
python3 - "<schema_url>" "<document_path>" <<'PY'
import sys
from analitiq.validator import main
sys.argv = ["analitiq-validate", "--schema-url", sys.argv[1], "--document", sys.argv[2]]
sys.exit(main())
PY
```

`--schema-url` is used only as a read/write **direction hint** for an
ambiguously-named type-map array.

## Findings

Do not expect a finding id per rule. Report every finding as the validator
emits it; never map one onto a rule id yourself. The id vocabulary is the
`Diagnostics` enum in `io-contracts.md`.

<!-- BEGIN GENERATED: validator-blind-spots -->
Checks the plugin's prose once claimed but the validator does **not** perform —
do not rely on them, and treat these as author-side discipline:

- **Function names are never checked.** An unregistered or misspelled
  `{"function": …}` passes validation and fails at connect time.
- **Ref *resolvability* is checked for exactly two things, one of them
  read-only.** On a READ, a `response.body.<path>` is resolved against
  `response.schema`; on either operation, a `response.metadata.<key>` is
  checked against the declared keys. Those typos are errors. Nothing else is
  proved, and three cases in particular look proved and are not:
  `response.records.<path>` and `response.headers.<name>` are spelling-checked
  only, and a WRITE mode has no `response.schema`, so no write-side
  `response.body` path is resolved — a `success_when` typo validates clean and
  the predicate then holds unconditionally. Every remaining scope is checked
  on its leading token only — so a `connection.discovered.*` ref with no
  post-auth output that produces it validates clean, on either document.
- **A connector field nothing resolves is not scope-checked either.** The
  leading-token check covers the fields a runtime actually resolves — the
  transports, the default header map, the auth exchange, the post-auth
  request, the DSN bindings. A `${...}` in a field consumed literally, such
  as a rate-limit window or a SQLAlchemy `options` entry, is refused by
  nobody and substituted by nobody: it reaches the driver as written.
- **TLS `ssl_mode` ↔ `ssl_ca_certificate` consistency is not checked.**
<!-- END GENERATED: validator-blind-spots -->

## Output

Print the JSON output of the validator verbatim — it is already a
`Diagnostics` document. Do not summarize, do not add prose, do not
reformat.

## Hard rules

- Never modify the document under validation.
- Never silence warnings. If `passed` is false, return the full finding list.
- If the command exits non-zero and stdout is not a valid `Diagnostics` JSON
  object (the self-install failed — no network or `pip` unavailable — or the
  validator crashed before emitting its report), report a single error finding
  (`validator: "contract-model"`, `severity: "error"`) describing the failure.
  Never forward partial or non-JSON stdout as the verdict.
