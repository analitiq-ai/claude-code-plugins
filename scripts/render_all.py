#!/usr/bin/env python3
"""Run every generator in this repo, in dependency order.

Usage:
    render_all.py write   # regenerate every derived artifact
    render_all.py check   # verify every derived artifact is current (CI)

This wrapper is the one place the generator set and its order are stated.
Everything that invokes the pipeline — the pre-commit hook in `.githooks/`,
the render hook in `.claude/settings.json`, the CI check step — calls this
script rather than naming generators itself, so adding a generator is one
edit here.

`write` stops at the first failure, because later generators consume earlier
output: the rule reference reads the registry `render_rules.py` compiles, and
the Contents sections `render_reference_toc.py` derives come from documents
the other renderers write into. `check` runs every generator regardless and
reports all failures at once, since each check only compares committed state.

A generator is only ever CHECKED here, never written, when its write mode
would press a judgment a pipeline must not press, or when it has no write
mode at all: `render_schemas.py write` cuts a new immutable schema version,
`render_prose_census.py write` restamps a changed prose site's hash — the
re-affirmation the census exists to demand from a person — and
`render_contract_consumption.py` has no write mode, because a disposition is
a judgment nothing here can make. When such a check fails, this script
fails, and the author does deliberately what that generator's output says.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"

# (script, write argvs, check argv). Order is the dependency order for write.
PIPELINE = [
    # schemas/ is rendered output of packages/contract-models; canonical-types
    # is rendered from the vendored engine grammar, and contracts-version is
    # the tree's provenance stamp. One check covers every one of them.
    # Checked in BOTH modes, never written here: `render_schemas.py write`
    # cuts a new schema version (--resource, --bump), a judgment about what
    # kind of contract change this is — its own failure output says how.
    ("render_schemas.py", [["check"]], ["check"]),
    # Validates every rules/records/*.yaml, resolves every `validator` binding
    # against the live models, and compiles the rules.json the wheel ships.
    # Before the renderers below: they all read the compiled registry.
    ("render_rules.py", [["write"]], ["check"]),
    # Each plugin's references/rules/ set, split by the artifact a rule binds.
    ("render_rule_reference.py", [["write"]], ["check"]),
    # Contract-owned facts (enums, regexes, bounds) rendered into the pipeline
    # plugin's generated prose blocks.
    ("gen_pipeline_docs.py", [[]], ["--check"]),
    # Probes the validator and renders the generated blocks behind every
    # prose claim about what it does or does not check.
    ("render_validator_claims.py", [["write"]], ["check"]),
    # Checked in BOTH modes, never written here: `write` restamps a changed
    # prose site's hash, which IS the re-affirmation the census exists to
    # demand from a person — an automated restamp would press it silently.
    ("render_prose_census.py", [["check"]], ["check"]),
    # Check-only because it HAS no write mode: a disposition is a judgment
    # about an unread field, and nothing here can make one.
    ("render_contract_consumption.py", [["check"]], ["check"]),
    # Last: Contents sections derive from the documents' final headings, which
    # the renderers above may have just rewritten.
    ("render_reference_toc.py", [["write"]], ["check"]),
]


def _run(script: str, argv: list[str]) -> int:
    cmd = [sys.executable, str(SCRIPTS / script), *argv]
    print(f"$ {script} {' '.join(argv)}".rstrip(), flush=True)
    return subprocess.run(cmd, cwd=REPO_ROOT, check=False).returncode


def main(argv: list[str]) -> int:
    if argv not in (["write"], ["check"]):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    mode = argv[0]
    failed: list[str] = []
    for script, write_argvs, check_argv in PIPELINE:
        for args in write_argvs if mode == "write" else [check_argv]:
            if _run(script, args) != 0:
                if mode == "write":
                    print(f"{script}: failed — stopping; nothing after it "
                          "ran. Fix the failure it printed above.",
                          file=sys.stderr)
                    return 1
                failed.append(script)
                break
    if failed:
        print(f"failing: {', '.join(failed)}. Stale output is fixed by "
              "`python3 scripts/render_all.py write`; a crash or a "
              "schemas/census failure needs what its own message says.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
