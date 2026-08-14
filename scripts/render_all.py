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

`render_prose_census.py write` restamps hashes but never invents dispositions,
and exits non-zero while manual work remains — so a run of this script cannot
silently affirm a census judgment; it stops and says what is left.
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
    # is rendered from the vendored engine grammar. One check covers both.
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
    # Restamps census hashes for changed contract prose; prints skeletons for
    # uncatalogued sites and fails while a disposition is still a human's call.
    ("render_prose_census.py", [["write"]], ["check"]),
    # Last: Contents sections derive from the documents' final headings, which
    # the renderers above may have just rewritten.
    ("render_reference_toc.py", [["write"]], ["check"]),
]


def _run(script: str, argv: list[str]) -> int:
    cmd = [sys.executable, str(SCRIPTS / script), *argv]
    print(f"$ {script} {' '.join(argv)}".rstrip(), flush=True)
    return subprocess.run(cmd, cwd=REPO_ROOT).returncode


def main(argv: list[str]) -> int:
    if argv != ["write"] and argv != ["check"]:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    mode = argv[0]
    failed: list[str] = []
    for script, write_argvs, check_argv in PIPELINE:
        for args in write_argvs if mode == "write" else [check_argv]:
            if _run(script, args) != 0:
                if mode == "write":
                    print(f"{script}: failed — stopping, later generators "
                          "consume its output", file=sys.stderr)
                    return 1
                failed.append(script)
                break
    if failed:
        print(f"stale or failing: {', '.join(failed)} — run "
              "`python3 scripts/render_all.py write`", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
