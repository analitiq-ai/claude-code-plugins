"""Drift guard for the generated cross-field-rule reference.

`plugins/analitiq-connector-builder/skills/connector-builder/references/rules.md` is rendered from the
pinned contract models' advisory registry so agent prose can cite a rule by id
instead of restating it. A generated copy is only safe while it is pinned: this
test regenerates it and fails when the checked-in file is stale, so a contract
change lands as a red build instead of silently-wrong authoring guidance.

It also guards the *citations*: prose cites rules by id (`RULE-ENDP-009`) instead
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
SCRIPT_PATH = REPO_ROOT / "scripts" / "render_rule_reference.py"


def _load_renderer():
    """Import the generator by path — `scripts/` is not an installed package."""
    spec = importlib.util.spec_from_file_location("render_rule_reference", SCRIPT_PATH)
    assert spec and spec.loader, f"cannot load {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rule_reference_is_in_sync() -> None:
    renderer = _load_renderer()
    expected = renderer.render()
    output_path = renderer.OUTPUT_PATH

    assert output_path.exists(), (
        f"{output_path.relative_to(REPO_ROOT)} is missing — "
        "run `python scripts/render_rule_reference.py write`"
    )
    assert output_path.read_text(encoding="utf-8") == expected, (
        f"{output_path.relative_to(REPO_ROOT)} is stale — the contract's advisory "
        "registry changed. Run `python scripts/render_rule_reference.py write` and review "
        "any prose that cites the affected rule ids."
    )


# Prose abbreviates groups of ids two ways: `RULE-TMAP-001/002` for a handful and
# `RULE-TMAP-001…007` for a run. Both tails must be expanded — a guard that saw
# only the leading id would miss exactly the dangling citation it exists to
# catch. (Both forms are in use, and each was introduced *after* the guard, so
# treat any new abbreviation as needing support here.)
ADV_ID_RE = re.compile(
    r"RULE-([A-Z]+)-(\d+)"          # the leading id
    r"((?:/\d+)*)"                 # `/002/003` enumeration
    r"(?:\s*(?:…|\.\.\.)\s*(\d+))?"  # `…007` range end
)


def _cited_ids(text: str) -> set[str]:
    """Expand every `RULE-*` citation, including `/` lists and `…` ranges."""
    found: set[str] = set()
    for prefix, first, enumerated, range_end in ADV_ID_RE.findall(text):
        width = len(first)
        found.add(f"RULE-{prefix}-{first}")
        for suffix in filter(None, enumerated.split("/")):
            found.add(f"RULE-{prefix}-{suffix}")
        if range_end:
            for n in range(int(first), int(range_end) + 1):
                found.add(f"RULE-{prefix}-{n:0{width}d}")
    return found


def test_prose_rule_citations_resolve() -> None:
    """Every `RULE-*` id cited in prose must name a rule that still exists.

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
    from analitiq.contracts.shared.rules import all_rules

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
        "no RULE-* citations found under plugins/ — plugin prose cites dozens, "
        "so the search glob no longer points at it — a scan over zero citations "
        "would otherwise pass vacuously."
    )
    assert not dangling, (
        f"prose cites rule ids that no longer exist: {dangling}. Update the "
        "citation to the current rule, or restate the constraint if the rule "
        "was retired."
    )


def test_values_column_labels_a_field_by_its_wire_alias() -> None:
    """The rendered vocabulary is labelled with the key an author types.

    The record names a Python attribute, and for two fields that is not what
    the document spells: `Param.location` and `Idempotency.location` both
    publish as `in`. Labelling the row with the attribute is not a cosmetic
    slip — the models set `populate_by_name`, so a connector authored under the
    attribute name passes the local validator and is then rejected by the
    published schema, which requires the alias and forbids unknown keys. The
    failure lands after the connector ships.

    Prose used to carry these vocabularies inline and no longer does, so this
    column is the only place an agent can read them. Derived from the models
    rather than asserted against a literal, so a field that gains or loses an
    alias is covered without editing this test.
    """
    renderer = _load_renderer()
    from analitiq.contracts.shared.rules import all_rules

    models = renderer._model_index()
    checked = 0
    for rule in all_rules():
        if rule.mechanism != "literal_enum":
            continue
        rendered = renderer._live_values(rule, models)
        for target in rule.targets:
            model = models.get(target)
            if model is None:
                continue
            for expr in rule.fields:
                head = expr.split("[]")[0].split(".")[0]
                info = model.model_fields.get(head)
                if info is None or not info.alias or info.alias == head:
                    continue
                checked += 1
                assert f"`{info.alias}`:" in rendered, (
                    f"{rule.id} renders {target}.{head} without its wire alias "
                    f"{info.alias!r}: {rendered!r}. An author would type the "
                    "attribute name, which the local validator accepts and the "
                    "published schema rejects."
                )
                assert f"`{head}`:" not in rendered, (
                    f"{rule.id} labels {target}.{head} with the Python "
                    f"attribute rather than {info.alias!r}: {rendered!r}"
                )
    assert checked, (
        "no alias-bearing field reached this guard — every `literal_enum` rule "
        "now names a field whose attribute and wire key agree, or the record "
        "shape changed. Re-point it before deleting it."
    )


def test_one_field_renders_as_one_row_across_a_rules_targets() -> None:
    """A rule over a union collects one field's members from every branch.

    The row is keyed on the record's own field spelling, not on the resolved
    wire label. Keying on the label splits a rule whose branches alias the
    field differently into a row per spelling — each an incomplete vocabulary
    presented as a whole one, which is the failure this column exists to
    prevent and which nothing downstream could detect. Branches that agree
    merge into one row; branches that disagree have no single row to render, so
    the renderer says so instead of picking one.

    Synthetic models rather than contract ones: no rule in the registry has
    disagreeing branches today, so the real tree cannot exercise either path,
    and a guard that only runs once the defect has shipped is not a guard.
    """
    from typing import Literal

    import pytest
    from pydantic import BaseModel, Field

    renderer = _load_renderer()

    class GetBranch(BaseModel):
        location: Literal["path", "query"] = Field(..., alias="in")

    class PostBranch(BaseModel):
        location: Literal["body"] = Field(..., alias="in")

    class RenamedBranch(BaseModel):
        location: Literal["body"] = Field(..., alias="where")

    class Rule:
        id = "RULE-TEST-001"
        mechanism = "literal_enum"
        fields = ("location",)

        def __init__(self, targets):
            self.targets = targets

    merged = renderer._live_values(
        Rule(["GetBranch", "PostBranch"]),
        {"GetBranch": GetBranch, "PostBranch": PostBranch},
    )
    assert merged == "`in`: `path`, `query`, `body`", (
        f"branches agreeing on the wire name must merge into one row: {merged!r}"
    )

    with pytest.raises(ValueError, match="disagree on the wire name"):
        renderer._live_values(
            Rule(["GetBranch", "RenamedBranch"]),
            {"GetBranch": GetBranch, "RenamedBranch": RenamedBranch},
        )
