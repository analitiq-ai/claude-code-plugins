"""A record's `artifact_kinds` must agree with which documents reach its `targets`.

The defect class this closes: a rule whose statement binds one artifact's
author while its `artifact_kinds` name another, so the reference renders it
into a file that author never opens — and the per-scope headers tell them no
other file applies. Without this comparison, each instance is only ever found
by a person reading one record.

The comparison is structural, not semantic: for every artifact kind there is a
set of document root models, and a model is *reachable* from an artifact kind
when some root's field graph reaches it. A record's declared artifact kinds
must then match the artifact kinds its targets are reachable from, in both
directions — a target no declared kind reaches is a rule filed where its
subject cannot appear, and a kind reaching a target that is not declared is an
author the rule binds and never meets.

What this cannot decide: a rule on a model reachable from several documents
whose *statement* genuinely binds only one of those authors. That narrowing is
semantic, so it is declared per record in `NARROWED`, with the reason — the
registry-waiver shape, a reviewable state instead of an absence.
"""
from __future__ import annotations

import typing
from dataclasses import dataclass


from _pins import require_contract_models

require_contract_models("analitiq.contracts")


def _roots() -> dict[str, tuple[type, ...]]:
    from analitiq.contracts import connection, connector, endpoints, stream, type_map
    from analitiq.contracts.pipelines import config

    return {
        "connector": (
            connector.ApiConnector, connector.DatabaseConnector,
            connector.NosqlConnector, connector.DocumentConnector,
            connector.FileConnector, connector.S3Connector,
            connector.StdoutConnector,
        ),
        "connection": (connection.ConnectionInput,),
        "api-endpoint": (endpoints.ApiEndpointDoc,),
        "database-endpoint": (endpoints.DatabaseEndpointDoc,),
        "stream": (stream.StreamInput,),
        "pipeline": (config.PipelineInput,),
        "type-map": (type_map.TypeMapReadDoc, type_map.TypeMapWriteDoc),
    }


#: Artifact kinds deliberately given no document root: `connector-package` is
#: a repository layout no model renders, `any` binds every document by fiat,
#: and `data-sync-run-status` describes a wire payload whose schema is
#: hand-maintained outside the model tree. Declared rather than derived — a
#: new ARTIFACT_KINDS member must be classified here or in `_roots()`, and the
#: partition test below is what refuses an unclassified one. Deriving this as
#: ARTIFACT_KINDS-minus-roots would silently exempt a new document family
#: whose root nobody wired in.
UNROOTED = {"connector-package", "any", "data-sync-run-status"}

#: Records whose statement semantically narrows a structurally wider reach —
#: the model is reachable from these kinds too, but the sentence does not
#: bind that author. Each entry is (record id, artifact kind it deliberately
#: omits) with the reason. An entry that stops being true fails the stale
#: check.
#:
#: The stream entries share one reason: a stream reaches `DatabaseObject`
#: only inside a connection-scoped endpoint ref, which the stream rules
#: require to carry the endpoint document's identity VERBATIM — so the
#: obligations about how that identity is authored bind the endpoint author,
#: and the stream author's transcription duty is its own rule. Contrast
#: RULE-DBEP-007, which is scoped to `stream` because parsing the derived
#: handle back is precisely what a ref-holder would do.
NARROWED: dict[tuple[str, str], str] = {
    ("RULE-DBEP-005", "stream"):
        "recording the system's namespace levels is done where the object is "
        "authored; the stream copies the result verbatim",
    ("RULE-DBEP-009", "stream"):
        "identifier verbatimness is decided where the object is authored; "
        "the stream copies the result verbatim",
    ("RULE-DBEP-010", "stream"):
        "targeting a discovered namespace is an endpoint-authoring decision; "
        "the stream copies the resulting identity verbatim",
    ("RULE-DBEP-013", "stream"):
        "branching on the type label is an execution/consumer conduct rule; "
        "a stream carries the object identity and never reads the label",
}


def _model_fields(cls: type) -> dict:
    return getattr(cls, "model_fields", {})


def _classes_in(annotation) -> set[type]:
    found: set[type] = set()
    stack = [annotation]
    while stack:
        node = stack.pop()
        if isinstance(node, type):
            if _model_fields(node):
                found.add(node)
            continue
        stack.extend(typing.get_args(node))
    return found


def _reachable_names(roots: tuple[type, ...]) -> set[str]:
    seen: set[type] = set()
    stack: list[type] = list(roots)
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        # RootModel documents carry their payload in the `root` field like any
        # other; model_fields covers it.
        for field in _model_fields(cls).values():
            stack.extend(_classes_in(field.annotation))
    return {base.__name__ for cls in seen for base in cls.__mro__}


@dataclass(frozen=True)
class _Verdict:
    record: str
    artifact_kind: str
    direction: str


def _mismatches() -> list[_Verdict]:
    from analitiq.contracts.shared.rules import all_rules

    roots = _roots()
    reachable = {kind: _reachable_names(classes) for kind, classes in roots.items()}
    out: list[_Verdict] = []
    for rule in all_rules():
        if not rule.targets or "any" in rule.artifact_kinds:
            continue
        implied = {
            kind for kind, names in reachable.items()
            if any(target in names for target in rule.targets)
        }
        declared = set(rule.artifact_kinds) - UNROOTED
        for kind in sorted(implied - declared):
            if (rule.id, kind) not in NARROWED:
                out.append(_Verdict(rule.id, kind, "missing"))
        for kind in sorted(declared - implied):
            out.append(_Verdict(rule.id, kind, "unreachable"))
    return out


def test_every_artifact_kind_matches_where_the_targets_live() -> None:
    mismatches = _mismatches()
    lines = [
        f"  {v.record}: artifact kind '{v.artifact_kind}' is {v.direction} — "
        + ("that document reaches a target, so its author is bound and never "
           "meets the rule; add the kind or a NARROWED entry saying why not."
           if v.direction == "missing" else
           "no document of that kind reaches any target; the rule is filed "
           "where its subject cannot appear.")
        for v in mismatches
    ]
    assert not mismatches, (
        "record artifact_kinds disagree with target reachability:\n" + "\n".join(lines)
    )


def test_narrowed_entries_are_still_live() -> None:
    """A NARROWED entry must still describe a real structural reach."""
    from analitiq.contracts.shared.rules import all_rules

    reachable = {k: _reachable_names(c) for k, c in _roots().items()}
    rules = {r.id: r for r in all_rules()}
    stale = [
        (rid, kind) for rid, kind in NARROWED
        if rid not in rules
        or kind in rules[rid].artifact_kinds
        or not any(t in reachable.get(kind, set()) for t in rules[rid].targets)
    ]
    assert not stale, (
        f"NARROWED entries no longer describe a narrowing: {stale} — the "
        "record moved; delete or update the entry."
    )


def test_the_guard_is_not_vacuous() -> None:
    """Most records carry targets; zero comparisons means the walk broke."""
    from analitiq.contracts.shared.rules import all_rules

    compared = [r for r in all_rules() if r.targets and "any" not in r.artifact_kinds]
    assert len(compared) > 100, len(compared)
    reachable = _reachable_names(_roots()["stream"])
    assert "ConnectionEndpointRef" in reachable and "DatabaseObject" in reachable


def test_every_artifact_kind_is_rooted_or_declared_unrooted() -> None:
    """The partition that keeps a new ARTIFACT_KINDS member from being
    silently exempt: it must gain a root in `_roots()` or a reasoned
    UNROOTED entry."""
    from analitiq.contracts.shared.rule_record import ARTIFACT_KINDS

    assert set(_roots()) | UNROOTED == set(ARTIFACT_KINDS)
    assert not set(_roots()) & UNROOTED
