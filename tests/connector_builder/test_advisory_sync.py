"""Drift guard for the generated cross-field-rule reference.

`plugins/analitiq-connector-builder/skills/connector-builder/references/advisory-rules.md` is rendered from the
pinned contract models' advisory registry so agent prose can cite a rule by id
instead of restating it. A generated copy is only safe while it is pinned: this
test regenerates it and fails when the checked-in file is stale, so a contract
change lands as a red build instead of silently-wrong authoring guidance.

It also guards the *citations*: prose cites rules by id (`ADV-ENDP-009`) instead
of restating them, so a retired or renumbered id must not be allowed to leave
dangling references behind a green build. That gate spans EVERY plugin under
`plugins/`, not just this suite's — the advisory registry is one shared source,
so one scan pins every citation site the prose currently has, all plugins plus
the repo-root docs; a per-plugin copy of the scanner would itself be a drift
surface.

Same environment contract as `test_schema_drift.py`: skipped when the pinned
package is absent (offline dev), hard-failed in CI via
`DRIFT_REQUIRE_CONTRACT_MODELS=1`.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from _pins import require_contract_models

require_contract_models("analitiq.contracts")

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "render_advisory.py"


def _load_renderer():
    """Import the generator by path — `scripts/` is not an installed package."""
    spec = importlib.util.spec_from_file_location("render_advisory", SCRIPT_PATH)
    assert spec and spec.loader, f"cannot load {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_advisory_reference_is_in_sync() -> None:
    renderer = _load_renderer()
    expected = renderer.render()
    output_path = renderer.OUTPUT_PATH

    assert output_path.exists(), (
        f"{output_path.relative_to(REPO_ROOT)} is missing — "
        "run `python scripts/render_advisory.py write`"
    )
    assert output_path.read_text(encoding="utf-8") == expected, (
        f"{output_path.relative_to(REPO_ROOT)} is stale — the contract's advisory "
        "registry changed. Run `python scripts/render_advisory.py write` and review "
        "any prose that cites the affected rule ids."
    )


def test_reference_covers_only_authored_resources() -> None:
    """The reference must not leak rules for documents this plugin never authors.

    Pipelines, streams, connection documents, and database endpoints belong to
    other tools; carrying their rules here would invite agents to enforce rules
    against documents they do not own.

    "Carrying" means occupying a row — the id in a table's first cell. An
    out-of-scope id NAMED inside another rule's text is the opposite of a leak:
    that is how a waived rule says which enforcer stops short of it, and the
    reader learns a boundary rather than a rule to apply. A plain substring
    search cannot tell those apart, so it would force the registry to describe
    its own gaps vaguely to keep this green.
    """
    from analitiq.contracts.shared.advisory import all_rules

    renderer = _load_renderer()
    rendered = renderer.OUTPUT_PATH.read_text(encoding="utf-8")
    listed = {
        line.split("|")[1].strip()
        for line in rendered.splitlines()
        if line.startswith("| ADV-")
    }
    assert listed, "no rule rows found — the renderer's table shape moved"

    foreign = [
        rule
        for rule in all_rules()
        if rule.scope not in renderer.PLUGIN_SCOPES
    ]
    assert foreign, "expected the registry to carry rules outside the plugin's scope"

    borrowed = set(renderer.BORROWED_RULE_IDS)
    leaked = sorted(rule.id for rule in foreign if rule.id in listed - borrowed)
    assert not leaked, (
        f"reference leaked out-of-scope rule ids: {leaked}. A rule on a document "
        "this plugin does not author reaches the reference only by being named in "
        "BORROWED_RULE_IDS, which is a per-id argument, not a resource."
    )


def test_borrowed_rules_are_actually_cited() -> None:
    """The borrowed list is an exception, and an exception has to be earned.

    Each id is there because some prose site cites it to mark a boundary. Let
    the citation go and the entry becomes a foreign rule sitting in the
    reference with nothing asking for it — which is the leak the test above
    exists to prevent, arriving through the door built to allow it.
    """
    renderer = _load_renderer()
    prose = "".join(
        path.read_text(encoding="utf-8")
        for path in (REPO_ROOT / "plugins" / "analitiq-connector-builder").rglob("*.md")
        if path != renderer.OUTPUT_PATH
    )
    unwanted = [i for i in renderer.BORROWED_RULE_IDS if i not in prose]
    assert not unwanted, (
        f"BORROWED_RULE_IDS carries ids no prose in this plugin cites: {unwanted}. "
        "Drop them from the list — they are somebody else's rules."
    )


def test_scope_covers_every_authored_resource() -> None:
    """A newly-added in-scope artifact must not slip past the renderer.

    `PLUGIN_SCOPES` is an allowlist, so adding a scope to the registry leaves
    the rendered output byte-identical and the sync test green while its rules
    go missing. Pin the complement instead: everything excluded must be an
    artifact this plugin genuinely does not author.
    """
    from analitiq.contracts.shared.advisory import all_rules

    renderer = _load_renderer()
    # Documents owned by other tools: pipelines, streams, connection documents,
    # run status, and database endpoints (generated at runtime by the
    # connector's `resource_discovery`, never authored here).
    known_foreign = {
        "pipeline",
        "stream",
        "connection",
        "data-sync-run-status",
        "database-endpoint",
    }
    scopes = {rule.scope for rule in all_rules()}
    unclassified = scopes - set(renderer.PLUGIN_SCOPES) - known_foreign

    assert not unclassified, (
        f"contract added resource(s) {sorted(unclassified)} — decide whether this "
        "plugin authors them. If yes, add to PLUGIN_SCOPES in "
        "scripts/render_advisory.py and regenerate; if no, add to known_foreign here."
    )


# Prose abbreviates groups of ids two ways: `ADV-TMAP-001/002` for a handful and
# `ADV-TMAP-001…007` for a run. Both tails must be expanded — a guard that saw
# only the leading id would miss exactly the dangling citation it exists to
# catch. (Both forms are in use, and each was introduced *after* the guard, so
# treat any new abbreviation as needing support here.)
ADV_ID_RE = re.compile(
    r"ADV-([A-Z]+)-(\d+)"          # the leading id
    r"((?:/\d+)*)"                 # `/002/003` enumeration
    r"(?:\s*(?:…|\.\.\.)\s*(\d+))?"  # `…007` range end
)


def _cited_ids(text: str) -> set[str]:
    """Expand every `ADV-*` citation, including `/` lists and `…` ranges."""
    found: set[str] = set()
    for prefix, first, enumerated, range_end in ADV_ID_RE.findall(text):
        width = len(first)
        found.add(f"ADV-{prefix}-{first}")
        for suffix in filter(None, enumerated.split("/")):
            found.add(f"ADV-{prefix}-{suffix}")
        if range_end:
            for n in range(int(first), int(range_end) + 1):
                found.add(f"ADV-{prefix}-{n:0{width}d}")
    return found


def test_prose_rule_citations_resolve() -> None:
    """Every `ADV-*` id cited in prose must name a rule that still exists.

    Prose cites rules by id instead of restating them — which only works while
    the ids resolve. A retired or renumbered rule would otherwise leave dangling
    citations behind a green build.

    Scope is every `*.md` under `plugins/` (all plugins — the registry they cite
    is shared) plus the repo-root docs. The pre-monorepo globs scanned
    `REPO_ROOT/src`; the move of that tree to `plugins/` did not repoint the
    scan, so the gate ran vacuously green over zero citations and every pipeline
    citation sat unpinned. Hence the found-citations assert below, which turns a
    fully-vacuous plugins scope into a red build instead of a silent exemption.
    """
    from analitiq.contracts.shared.advisory import all_rules

    known = {rule.id for rule in all_rules()}
    generated = _load_renderer().OUTPUT_PATH

    dangling: dict[str, set[str]] = {}
    plugins_root = REPO_ROOT / "plugins"
    plugin_cited = 0
    for path in [*REPO_ROOT.glob("*.md"), *plugins_root.rglob("*.md")]:
        if path == generated:
            continue  # generated from the registry; covered by the sync test
        ids = _cited_ids(path.read_text(encoding="utf-8"))
        if plugins_root in path.parents:
            plugin_cited += len(ids)
        if ids - known:
            dangling[str(path.relative_to(REPO_ROOT))] = ids - known

    # Count the plugins' contribution specifically: a repo-root doc citing a
    # single id must not keep this green while the plugins glob rots the way
    # the src/ one did.
    assert plugin_cited, (
        "no ADV-* citations found under plugins/ — plugin prose cites dozens, "
        "so the search glob no longer points at it — a scan over zero citations "
        "would otherwise pass vacuously."
    )
    assert not dangling, (
        f"prose cites rule ids that no longer exist: {dangling}. Update the "
        "citation to the current rule, or restate the constraint if the rule "
        "was retired."
    )
