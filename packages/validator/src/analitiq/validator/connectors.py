"""Connector-package validation — the connector / api-endpoint / database-endpoint
/ type-map artifact kinds.

Single-document validity is delegated to the Pydantic **contract models**
(`analitiq-contract-models`, the same models the published JSON Schemas are
generated from): each document kind is validated with `TypeAdapter(...).
validate_python`, which enforces structure *and* every cross-field rule the
contract defines — offline, no schema fetch, no drift. On top of the models this
module adds only what a single-document model cannot express:

- **cross-file coverage** (`RULE-PKG-030`/`RULE-PKG-033`/`RULE-PKG-035`): a
  connector's sibling `type-map.json` must carry a rule list for each direction
  its kind needs, and an API connector's read rules must cover every
  `(native_type, arrow_type)` its endpoint files declare;
- **filename ↔ id** (`RULE-PKG-031`): an endpoint file must be named
  `{endpoint_id}.json`;
- **endpoint id uniqueness** (`RULE-PKG-032`): each `endpoint_id` is unique
  within the connector release;
- **endpoint id ↔ locator** (`RULE-ENDP-046`/`RULE-DBEP-011`): an `endpoint_id`
  equals the handle derived from its locator — an API id from its
  `operations.*.request.path` (lowercase, `__` between path levels, path-params
  dropped) so `/v1/x` and `/v2/x` cannot collide (`RULE-ENDP-046` requires it;
  the algorithm is
  `plugins/analitiq-connector-builder/skills/connector-builder/references/io-contracts.md`'s
  `resources[].key` description);
  a database id from its verbatim
  `database_object` (`slug(schema)__slug(table)[__slug(catalog)]__hash8`, via the
  shared `analitiq.contracts.endpoint_identity`);
- **endpoint → transport** (`RULE-ENDP-047`): an endpoint's
  `request.transport_ref` must name a transport the sibling connector.json
  declares. `ConnectorBase._transport_refs_resolvable` enforces the same rule for
  every connector-internal ref site, but an endpoint document is a separate file
  and structurally invisible to that model validator — so the cross-file half of
  the rule lives here;
- **advisory quality warnings** the contract tolerates: duplicate type-map
  rules, read patterns spelling a lowercase literal, regex natives spelling a
  container that renders a scalar, and write-rule vocabulary gaps
  (`RULE-TMAP-022`/`RULE-TMAP-014`/`RULE-TMAP-002`/`RULE-TMAP-017`).

At import this module registers each validator under the published document
schema name it grades, so `_core` never hard-codes connector branches.
"""
from __future__ import annotations

import json
import re
import reprlib
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePath
from typing import Any, Callable, Iterator, Literal, TypeVar

from ._core import (
    _JSON_TEXT_REFUSALS,
    contract_model_domain,
    finding,
    qualified,
    register_kind,
    _bounded,
    _model_findings,
    model_and_schema_findings,
    _run_guarded,
)
from ._location import Location, located, reference
from ._sample_budget import BudgetedGrader

# The contract models resolve from the `analitiq-contract-models` dependency —
# the same import path here and for an installed consumer, so there is nothing to
# rewrite on release. They bind `DOMAIN` at import for the `$schema` host
# `Literal`, so import them under the shared `contract_model_domain()` guard
# (which pins `analitiq.ai` for the import window and restores the caller's
# ambient `DOMAIN`).
try:
    with contract_model_domain():
        from pydantic import TypeAdapter
        from analitiq.contracts.connector import Connector
        from analitiq.contracts.endpoints import (
            ApiEndpointDoc,
            DatabaseEndpointDoc,
            SLUG_RE,
        )
        from analitiq.contracts.shared.json_schema import (
            escape_pointer_token,
            pointer_position,
            walk_structural_positions,
        )
        from analitiq.contracts.endpoint_identity import derive_db_endpoint_id
        from analitiq.contracts.type_map import TYPE_MAP_DIRECTIONS, TypeMapDoc
        # Reuse the contract's matcher compilation, `${name}` placeholder
        # syntax and container test from the model so the validator's
        # rule-rendering and warnings can't drift from the model's rule-validation.
        from analitiq.contracts.type_map import (
            _PLACEHOLDER_RE, _guard_container_not_collapsed, compile_matcher,
        )
        # The executable Arrow vocabulary — the write-coverage probe set is
        # derived from it rather than sampled by hand.
        from analitiq.contracts import arrow_grammar
        # The single source of truth for read-match normalization — imported, not
        # re-implemented, so the validator's coverage check normalizes exactly as
        # every runtime reader does (`analitiq.contracts.type_map`).
        from analitiq.contracts.type_map import normalize_native_type as _normalize_native
except ImportError as exc:  # pragma: no cover - dependency guard
    # notApplicable, not fail: nothing here decided whether any rule holds —
    # the dependency itself is missing, so no rule was even reachable to ask
    # about. Naming none keeps it in the framework's no-rule case, which
    # always costs `passed` the same way an unconditional error once did.
    print(json.dumps({
        "passed": False,
        "findings": [finding(
            message_id="missing-contract-models-dependency",
            kind="notApplicable", path="",
            message=f"Missing dependency: {exc}. Install `analitiq-contract-models`.")],
    }))
    sys.exit(1)


TYPE_MAP_FILENAME = "type-map.json"
# Names that read as a type map and are not the one a `definition/` directory
# holds. A file under one carries rules nothing grades, beside a package that
# looks as though it ships them.
_STRAY_TYPE_MAP_GLOB = "type-map-*.json"

_STORAGE_KINDS = ("file", "s3", "stdout")
# Database-family kinds own database-endpoint documents and need a read section
# (source) and a write section (destination DDL rendering).
_DATABASE_KINDS = ("database", "nosql", "document")


# ---------------------------------------------------------------------------
# Type-map rendering (read/write coverage) — cross-file / advisory only
# ---------------------------------------------------------------------------

_NARROWING_ARROW_TYPES = {"Object", "List"}


def _first_match_render(value: str, rules: list, matcher_key: str, render_key: str,
                        normalize: Callable[[str], str] | None = None) -> str | None:
    """First-match-wins render; substitutes `${name}` from regex captures.

    For read maps `normalize` is `normalize_native_type` (imported as
    `_normalize_native`) and is applied to BOTH sides of an `exact`
    comparison — the incoming probe and the rule's `native_type` matcher —
    because every runtime reader normalizes an exact rule's `native_type` the
    same way it normalizes the lookup value, so the two must agree here too. A
    `regex` matcher is never normalized (uppercasing would turn `\\d` into
    `\\D`); only its probe is. `normalize` is None for write maps, where the
    `arrow_type` matcher is compared as authored.
    """
    probe = normalize(value) if normalize else value
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        matcher_value = rule.get(matcher_key)
        render_value = rule.get(render_key)
        if not isinstance(matcher_value, str) or not isinstance(render_value, str):
            continue
        if rule.get("match") == "exact":
            matcher = normalize(matcher_value) if normalize else matcher_value
            if matcher == probe:
                return render_value
        elif rule.get("match") == "regex":
            try:
                compiled = compile_matcher(matcher_value)
            except ValueError:
                # The model reports a matcher the contract refuses; it renders nothing.
                continue
            m = compiled.fullmatch(probe)
            if not m:
                continue
            groups = m.groupdict()
            return _PLACEHOLDER_RE.sub(
                lambda ph: groups.get(ph.group(1)) or "" if ph.group(1) in groups else ph.group(0),
                render_value,
            )
    return None


def _render_arrow_type(native_type: str, rules: list) -> str | None:
    return _first_match_render(native_type, rules, "native_type", "arrow_type",
                               normalize=_normalize_native)


# Collapse whitespace ONLY around Arrow separators — not inside identifiers.
_ARROW_SEP_WS = re.compile(r"\s*([,()])\s*")


def _arrow_type_eq(a: str, b: str) -> bool:
    """Compare two Arrow types ignoring separator spacing only. The
    intra-parameter spacing of `Decimal128(38, 9)` vs `Decimal128(38,9)` is not
    significant, but whitespace INSIDE a token IS (`Time stamp(SECOND)` is not
    `Timestamp(SECOND)`) — so whitespace is collapsed only around the Arrow
    separators (`,()`, the vocabulary's only SEPARATOR punctuation; `:`/`/`/`+`
    occur only inside timezone tokens, where whitespace stays significant),
    never deleted wholesale."""
    norm = lambda s: _ARROW_SEP_WS.sub(r"\1", s).strip()  # noqa: E731
    return norm(a) == norm(b)


def _embedded_json_schemas(ep_doc: dict) -> list[tuple[str, Any]]:
    """The endpoint's embedded JSON-Schema documents as `(pointer, schema)` —
    `operations.read.response.schema` and each `operations.write.<mode>.input.schema`
    (the top-level schema value itself, not walked)."""
    out: list[tuple[str, Any]] = []
    ops = ep_doc.get("operations")
    if not isinstance(ops, dict):
        return out
    read = ops.get("read")
    if isinstance(read, dict) and isinstance(read.get("response"), dict):
        out.append(("/operations/read/response/schema", read["response"].get("schema")))
    write = ops.get("write")
    if isinstance(write, dict):
        for mode, block in write.items():
            if isinstance(block, dict) and isinstance(block.get("input"), dict):
                out.append((f"/operations/write/{escape_pointer_token(mode)}/input/schema",
                            block["input"].get("schema")))
    return out


def _walk_schema_nodes(schema: Any, pointer: str) -> Iterator[tuple[str, dict]]:
    """Every structural sub-schema node of a JSON Schema document, as
    `(pointer, node)`. The document itself is a node and is yielded first, under
    the bare `pointer` given.

    One walk serves every check that has to reach a node: the `native_type` /
    `arrow_type` pairing and the grading of recorded samples must descend
    identically, or a node one of them cannot see is a node the other grades
    alone. That walk is the contract's
    :func:`analitiq.contracts.shared.json_schema.walk_structural_positions`, so
    the positions this validator reaches are the positions the contract models
    reach. `pointer_position` is its adapter into the pointer dialect a
    finding's `path` carries — the same adapter the contract's own endpoint
    walkers use, so the two can never disagree about how a token becomes a
    pointer segment. Non-dict values are filtered out — a boolean short-form
    declares no node to grade, and a malformed one is the meta-schema check's
    to report."""
    for tokens, node in walk_structural_positions(schema):
        if not isinstance(node, dict):
            continue
        yield pointer + pointer_position(tokens), node


def _collect_native_arrow_pairs(ep_doc: dict) -> list[tuple[str, str, str]]:
    """Every `(native_type, arrow_type)` pair on the endpoint's typed field
    schemas. Walks the schemas structurally so a field named `default`/`const`
    is covered but a literal-data value is not."""
    return [
        (node["native_type"], node["arrow_type"], node_ptr)
        for pointer, schema in _embedded_json_schemas(ep_doc)
        for node_ptr, node in _walk_schema_nodes(schema, pointer)
        if isinstance(node.get("native_type"), str) and isinstance(node.get("arrow_type"), str)
    ]


_DRAFT_2020_12_SCHEMA = "https://json-schema.org/draft/2020-12/schema"

#: Renders a recorded sample into a finding message. A sample may be a whole
#: provider record and a finding is read in a terminal, so it is bounded — by
#: `reprlib`, which bounds each component rather than the string as a whole, so
#: a record with one oversized field still shows the fields around it.
_SAMPLE_REPR = reprlib.Repr()
_SAMPLE_REPR.maxstring = _SAMPLE_REPR.maxother = 80


def _unreadable_as_2020_12(schema: dict) -> str | None:
    """Why this embedded document cannot be read as JSON Schema Draft 2020-12,
    phrased to follow "embedded schema at <site> " — or `None` when it can be.

    One verdict, because two checks turn on it: `_embedded_schema_findings`
    reports the reason and sample grading skips exactly what it reported. Asked
    separately the two could disagree about which documents are readable, and a
    document graded under semantics it does not declare earns findings about
    keywords that were never going to apply to it.

    Two ways to be unreadable, asked in the order that keeps the second one safe:

    - **Its keywords are not legal 2020-12**, which is what `check_schema` is.
      It also types `$schema`: the 2020-12 meta-schema declares the keyword a
      string, at the root and at every node alike, so a value the comparison
      below could not read is already a reason before that comparison runs.
    - **It claims another draft.** `check_schema` grades keywords against the
      2020-12 meta-schema and has no opinion on what a document claims to be,
      so this half is refused here or nowhere. The declaration is read at the
      root, which is the only place an embedded schema may carry one — the
      contract models refuse `$schema` on every subschema (RULE-ENDP-064).
      `…/2020-12/schema#` names the same resource as `…/2020-12/schema` under
      RFC 3986, so a single trailing empty fragment is stripped before the
      comparison — a single one, since `##` is not a spelling of this URI and
      naming another dialect is exactly what this half exists to catch. An
      absent `$schema` is fine: the engine reads an undeclared schema as
      2020-12, and a valid authored write `input.schema` may omit it.

    `jsonschema` is imported lazily HERE, not at module load: some callers import
    `analitiq.validator` only to run `validate_pipeline_bundle` and never reach
    this api-endpoint path, so a module-level import would force `jsonschema` onto
    every consumer even where it is not installed. Only endpoint meta-validation
    needs it."""
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        return (f"is not a valid JSON Schema Draft 2020-12 document: {exc.message}")
    declared = schema.get("$schema")
    if declared is not None and declared.removesuffix("#") != _DRAFT_2020_12_SCHEMA:
        return (f"declares $schema {declared!r}; the contract requires JSON Schema "
                f"Draft 2020-12 ({_DRAFT_2020_12_SCHEMA!r}) or no $schema")
    return None


def _embedded_schema_findings(ep_doc: dict) -> list[dict]:
    """Each embedded input/response schema must be a valid JSON Schema
    Draft 2020-12 document. The contract model checks the arrow_type/native_type
    pairing but not meta-schema validity, so this is the validator's job. A
    non-dict schema is already a recorded model error and is skipped here."""
    findings: list[dict] = []
    for pointer, schema in _embedded_json_schemas(ep_doc):
        if not isinstance(schema, dict):
            continue
        reason = _unreadable_as_2020_12(schema)
        if reason is not None:
            findings.append(finding(
                rule="RULE-ENDP-048",
                message_id="invalid-embedded-schema", kind="fail", path=pointer,
                message=f"embedded schema at {pointer} {reason}"))
    return findings


def _embedded_schema_example_findings(ep_doc: dict) -> list[dict]:
    """Every `examples` entry on an embedded schema node must satisfy the node
    declaring it.

    This is the only check over an endpoint that reads a value rather than
    another declaration. Every other one compares `native_type` to `arrow_type`
    to the sibling type map to the canonical vocabulary, and they agree because
    each reads the same claim restated — so a field declared boolean whose
    provider sends the strings `"0"` and `"1"` passes all of them and fails on
    the first batch, where the cast is attempted for real. A recorded sample is
    the one thing in the document that came off the wire, which makes it the one
    thing a declaration can be checked against.

    Samples stay optional. A node with no `examples` is graded on nothing, and
    silence is never read as agreement.

    Two things are skipped rather than graded, each because a check running
    beside this one reports it: a schema that is not a dict (the contract model
    rejects it) and one `_unreadable_as_2020_12` refuses (`_embedded_schema_findings`
    reports it). Every call site runs those together with this, which is what
    keeps a skip from being a silent pass. Grading either anyway would report
    keywords that were never going to apply, blaming the sample for the author's
    typo.

    Past that, grading runs keyword logic over an author-supplied value with no
    total gate ahead of it, so each entry is guarded on its own and the guard
    answers whose defect it is:

    - **the node's** — a reference naming nothing, or one leading back to itself;
    - **the sample's** — a value no keyword on the node can evaluate, such as a
      number too large for `multipleOf`;
    - **undecided** — an evaluation that did not finish inside its budget. Every
      other arm here contains a way the evaluation RAISES, and a keyword whose
      cost grows with the recorded value rather than with its size does not
      raise, it does not come back; `_sample_budget` owns why that cannot be
      waited on and where the bound is enforced;
    - **neither**, which is the contradiction this check exists to report.

    Each has a different fix, so they are worth telling apart, and none of them
    may cost the remaining entries their verdict."""
    findings: list[dict] = []
    with BudgetedGrader() as grader:
        for pointer, schema in _embedded_json_schemas(ep_doc):
            if not isinstance(schema, dict):
                continue
            if _unreadable_as_2020_12(schema) is not None:
                continue
            for node_ptr, node in _walk_schema_nodes(schema, pointer):
                examples = node.get("examples")
                if not isinstance(examples, list):
                    continue
                for index, sample in enumerate(examples):
                    entry = f"{node_ptr}/examples/{index}"
                    # `entry` is the finding's machine-readable `path` and stays
                    # exact. Everything the MESSAGE interpolates is bounded,
                    # pointers included: a property name is authored, so it can
                    # be as long as the author likes and it appears in the
                    # pointer twice.
                    where = _bounded(entry)
                    at_node = _bounded(node_ptr)
                    verdict = grader.grade(schema, node, sample)
                    verdict_kind = verdict["v"]
                    # Only "graded" decides the rule one way or the other; every
                    # other verdict means nothing here could tell, which is
                    # `notApplicable` against the same rule — the check knows
                    # exactly which node and sample it was grading, it just
                    # could not reach a verdict for this one.
                    finding_kind = "fail"
                    if verdict_kind == "graded":
                        error = verdict["error"]
                        if error is None:
                            continue
                        message_id = "sample-contradicts-schema"
                        inside = ("" if error["path"] == "$"
                                  else f" at {_bounded(error['path'])} within the sample")
                        message = (
                            f"the sample at {where} is {_SAMPLE_REPR.repr(sample)}, which the node "
                            f"declaring it rejects{inside}: {_bounded(error['message'])}. A sample is a "
                            f"value the provider sends, so either the declared shape is wrong for "
                            f"this field or the recorded sample never came off the wire.")
                    elif verdict_kind in ("unresolvable", "recursion"):
                        finding_kind = "notApplicable"
                        message_id = "schema-node-unresolvable"
                        # The reference alone, never the resource it was searched
                        # for in: that renders the whole embedded schema, and it
                        # would land in the message once per recorded sample.
                        why = (f"the reference {_bounded(verdict['ref'])} names nothing this "
                               f"schema defines"
                               if verdict_kind == "unresolvable" else
                               "resolving it ran out of stack — either a reference leading "
                               "back to itself, or a sample nested deeper than the resolver "
                               "follows")
                        message = (
                            f"the node at {at_node} could not be resolved, so the sample "
                            f"at {where} was not graded: {why}. The defect is in the "
                            f"schema, not in the sample.")
                    elif verdict_kind == "crash":
                        finding_kind = "notApplicable"
                        message_id = "sample-grading-crashed"
                        message = (
                            f"the sample at {where} is {_SAMPLE_REPR.repr(sample)}, which the "
                            f"node declaring it could not grade ({verdict['type']}: "
                            f"{_bounded(verdict['detail'])}). "
                            f"The recorded value is outside what a keyword on this node can "
                            f"evaluate.")
                    elif verdict_kind == "budget":
                        finding_kind = "notApplicable"
                        message_id = "sample-grading-exceeded-budget"
                        message = (
                            f"the sample at {where} was not graded: evaluating it against the "
                            f"node at {at_node} exceeded the {verdict['seconds']:g}s budget for "
                            f"one sample. A keyword whose cost grows with the recorded value "
                            f"rather than with its size cannot be interrupted, so nothing was "
                            f"decided about this sample.")
                    elif verdict_kind == "exhausted":
                        finding_kind = "notApplicable"
                        message_id = "sample-grading-budget-exhausted"
                        message = (
                            f"the sample at {where} was not graded: this document's grading "
                            f"budget was already spent by earlier samples, so this entry was "
                            f"never attempted and nothing was decided about it.")
                    elif verdict_kind == "unserializable":
                        finding_kind = "notApplicable"
                        message_id = "sample-not-json"
                        message = (
                            f"the sample at {where} was not graded: the schema recording it "
                            f"holds a value that is not JSON data, so it could not be handed to "
                            f"the grader. A recorded sample is a value read out of a JSON "
                            f"document, so a value that cannot be encoded as JSON reached this "
                            f"document from a caller that built it rather than parsed it.")
                    else:
                        # `unavailable`, and anything a future kind might be. Never
                        # raised: this runs inside the per-endpoint guard, which
                        # would replace every finding the document had earned with
                        # one generic "validator bug".
                        finding_kind = "notApplicable"
                        message_id = "sample-grading-unavailable"
                        why = verdict.get("reason") or f"the grader answered {verdict_kind!r}"
                        message = (
                            f"the sample at {where} was not graded: {why}. Samples are graded in "
                            f"a worker process so an evaluation that does not return can be "
                            f"abandoned; with no worker, nothing was decided about this sample.")
                    findings.append(finding(
                        rule="RULE-ENDP-063",
                        message_id=message_id, kind=finding_kind, path=entry,
                        message=message))
    return findings


# Representative Arrow types a write map should render; gaps are warnings (a
# dialect may override rendering for a family). `Object`/`List` are the bare
# shape markers destination columns carry verbatim when an API source hands over
# a struct/array field — the engine probes the write map with the document's
# `arrow_type` literally, so a map without rules for them hard-errors the stream
# at configuration.
#
# DERIVED from the vendored engine grammar, one probe per family, so a family
# the engine gains is probed the moment the pin moves rather than silently
# missed. What the derivation does NOT reach is stated as data below, not as an
# omission from a hand-written list.

#: Families deliberately left unprobed, each with the reason. Checked against
#: the manifest at import: an entry naming no family fails the build, so a
#: family the engine drops cannot leave a stale excuse behind.
_WRITE_PROBE_EXCLUDED_FAMILIES: dict[str, str] = {
    "Decimal256": (
        "probed via Decimal128 — a map whose decimal rule is narrowed to "
        "Decimal128 shows nothing here, which is why the spec sends authors to "
        "check it by hand"
    ),
    "FixedSizeBinary": (
        "byte_width is unbounded, so no single probe represents the family: a "
        "map rendering FixedSizeBinary(16) may still miss FixedSizeBinary(32)"
    ),
    "Time32": (
        "coarse-unit sibling of Time64; a map covering one commonly covers the "
        "other through a shared regex, so probing both doubles the warning for "
        "one authoring decision"
    ),
}


def _family_probe(name: str) -> str:
    """One representative Arrow type for a family: each REQUIRED parameter takes
    the first value its grammar allows, and optional parameters are omitted.

    Omitting optionals is why the tz-aware `Timestamp(<unit>, <tz>)` spelling is
    not probed while the bare one is — a variant-level gap this samples past.
    """
    params = arrow_grammar.FAMILIES[name].get("params") or ()
    required = [p for p in params if not p.get("optional")]
    if not required:
        return name
    args = []
    for param in required:
        if param["kind"] == "int":
            lo, _hi = arrow_grammar.resolved_int_bounds(param, list(params))
            args.append(str(lo))
        else:
            args.append(param["allowed"][0])
    return f"{name}({', '.join(args)})"


_unknown_exclusions = sorted(
    set(_WRITE_PROBE_EXCLUDED_FAMILIES) - set(arrow_grammar.FAMILY_NAMES)
)
if _unknown_exclusions:
    raise RuntimeError(
        f"write-coverage probe exclusions name no family in the vendored engine "
        f"grammar: {_unknown_exclusions}. The vocabulary moved — decide whether "
        "each is now probed or drop the entry."
    )

_WRITE_VOCABULARY_PROBES: tuple[str, ...] = tuple(
    _family_probe(name)
    for name in arrow_grammar.FAMILY_NAMES
    if name not in _WRITE_PROBE_EXCLUDED_FAMILIES
)


def _write_vocabulary_findings(rules: list) -> list[dict]:
    """Warn when a write map renders no rule for an Arrow family."""
    missing = [
        probe for probe in _WRITE_VOCABULARY_PROBES
        if _first_match_render(probe, rules, "arrow_type", "native_type") is None
    ]
    if not missing:
        return []
    return [finding(
        rule="RULE-TMAP-017",
        message_id="write-map-missing-family", kind="fail", path="/write",
        message=(
            f"the write section has no rule rendering these Arrow families: {missing}. "
            "If the dialect renders them via a column-type override this is expected; "
            "otherwise add rules so they materialize."),
    )]


# A rough reading of a regex matcher's literal characters: it drops RE2 syntax
# loosely and keeps an escaped punctuation character. A construct it does not
# model reads as literal text, which misplaces only a warning.
_MATCHER_SYNTAX = re.compile(
    r"\[\^?\]?(?:\[:[^\]]*:\]|\\.|[^\]\\])*\]"  # a class
    r"|\(\?P?<[^>]*>|\(\?[A-Za-z-]*[:)]"        # a group or flag opener
    r"|\\[pPx]\{[^}]*\}|\\[pP]?[A-Za-z0-9]"     # a letter or braced escape, `\Q`/`\E` among them
    r"|\\(?P<kept>.)"                            # an escaped punctuation character
    r"|\{\d+(?:,\d*)?\}|[\^$|*+?.()]"            # a count or an operator
)
# A flag group turning case-insensitivity on, anywhere in the matcher.
_CASE_INSENSITIVE_FLAG = re.compile(r"\(\?[A-Za-z]*i")


def _matcher_literal_text(matcher: str) -> str:
    return _MATCHER_SYNTAX.sub(r"\g<kept>", matcher)


def _type_map_rule_warnings(rules: list, direction: str) -> list[dict]:
    """Advisory (non-error) type-map checks the contract tolerates: duplicate
    rules (later ones unreachable), and read regex natives whose literal
    characters hold a lowercase letter or spell a container rendered as a
    scalar."""
    if not isinstance(rules, list):
        return []
    matcher_key = "native_type" if direction == "read" else "arrow_type"
    findings: list[dict] = []
    seen: set[tuple[Any, Any]] = set()
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        match, matcher = rule.get("match"), rule.get(matcher_key)
        # Dedup on the SAME normal form the read reader matches on: two `exact`
        # read rules differing only by case/whitespace collapse to one matcher
        # at runtime (first wins, the later one unreachable), so they must count
        # as duplicates here too. Regex and write matchers dedup verbatim.
        dedup_matcher = (
            _normalize_native(matcher)
            if direction == "read" and match == "exact" and isinstance(matcher, str)
            else matcher
        )
        key = (match, dedup_matcher)
        try:
            if key in seen:
                findings.append(finding(
                    rule="RULE-TMAP-022",
                    message_id="duplicate-type-map-rule", kind="fail", path=f"/{direction}/{i}",
                    message=(
                        f"duplicate rule for (match={match!r}, {matcher_key}={matcher!r}); "
                        "first-match-wins makes later duplicates unreachable."),
                ))
            else:
                seen.add(key)
        except TypeError:
            # `(match, matcher)` is unhashable (a malformed rule with a list/dict
            # matcher). Skip the duplicate check for it — this is an advisory
            # warning pass only; the model already rejects the malformed rule.
            pass
        if direction == "read" and match == "regex" and isinstance(matcher, str):
            literal = _matcher_literal_text(matcher)
            if not _CASE_INSENSITIVE_FLAG.search(matcher) and any(c.islower() for c in literal):
                findings.append(finding(
                    rule="RULE-TMAP-014",
                    message_id="regex-native-case-mismatch", kind="fail",
                    path=f"/{direction}/{i}/{matcher_key}",
                    message=(
                        f"regex {matcher_key} is matched against UPPERCASED natives; "
                        f"lowercase literals in {matcher!r} can never match."),
                ))
            if isinstance(rule.get("arrow_type"), str):
                try:
                    _guard_container_not_collapsed(matcher, literal, rule["arrow_type"])
                except ValueError as detail:
                    findings.append(finding(
                        rule="RULE-TMAP-002",
                        message_id="read-regex-container-collapsed", kind="fail",
                        path=f"/{direction}/{i}", message=str(detail),
                    ))
    return findings


# ---------------------------------------------------------------------------
# Cross-file checks
# ---------------------------------------------------------------------------

# A path segment that is a SINGLE `{name}` placeholder — dropped from the derived
# id (path-params are operation-level, not part of the resource locator). Only a
# pure one-placeholder segment matches: a mixed segment like `{id}-{slug}` is NOT
# dropped (a greedy `^\{.*\}$` would, collapsing `/x/{id}-{slug}` and `/x/{id}` to
# the same handle) — its literal `{`/`}` then make the handle non-charset-safe and
# the locator gate rejects it rather than silently colliding.
_PATH_PARAM_SEGMENT = re.compile(r"^\{[^{}]+\}$")


def _flatten_api_locator(path: str) -> str:
    """Derive an API `endpoint_id` handle from a request path — required by
    `RULE-ENDP-046`, per the algorithm
    plugins/analitiq-connector-builder/skills/connector-builder/references/io-contracts.md's
    `resources[].key` description states: lowercase,
    `__` between path levels, `{param}` segments dropped, every segment in order.
    The FULL path (not just the leaf) forms the id, which is what keeps `/v1/x`
    and `/v2/x` from colliding."""
    segments = [
        seg.lower() for seg in path.split("/")
        if seg and not _PATH_PARAM_SEGMENT.match(seg)
    ]
    return "__".join(segments)


def _api_operation_paths(ep_doc: dict) -> list[tuple[str, str]]:
    """`(pointer, request.path)` for every operation of an api-endpoint doc."""
    out: list[tuple[str, str]] = []
    ops = ep_doc.get("operations")
    if not isinstance(ops, dict):
        return out
    read = ops.get("read")
    if isinstance(read, dict) and isinstance(read.get("request"), dict) \
            and isinstance(read["request"].get("path"), str):
        out.append(("/operations/read/request/path", read["request"]["path"]))
    write = ops.get("write")
    if isinstance(write, dict):
        for mode, block in write.items():
            if isinstance(block, dict) and isinstance(block.get("request"), dict) \
                    and isinstance(block["request"].get("path"), str):
                out.append((f"/operations/write/{escape_pointer_token(mode)}/request/path",
                            block["request"]["path"]))
    return out


def _api_operation_transport_refs(ep_doc: dict) -> list[tuple[str, Any]]:
    """`(pointer, request.transport_ref)` for every operation of an api-endpoint
    doc that DECLARES one — the read request and each write mode's request.

    An absent or `null` `transport_ref` is omitted: it means "use the connector's
    `default_transport`", which is always resolvable, so there is nothing to
    check. Malformed shapes (a non-dict `operations`/`write` mode, a request that
    is not an object) are skipped rather than probed — they are already recorded
    as model errors, and this walk must not crash on them."""
    out: list[tuple[str, Any]] = []
    ops = ep_doc.get("operations")
    if not isinstance(ops, dict):
        return out

    def _collect(block: Any, pointer: str) -> None:
        if not isinstance(block, dict):
            return
        request = block.get("request")
        if not isinstance(request, dict) or request.get("transport_ref") is None:
            return
        out.append((f"{pointer}/request/transport_ref", request["transport_ref"]))

    _collect(ops.get("read"), "/operations/read")
    write = ops.get("write")
    if isinstance(write, dict):
        for mode, block in write.items():
            _collect(block, f"/operations/write/{escape_pointer_token(mode)}")
    return out


def _endpoint_transport_ref_findings(ep_doc: Any, transports: Any) -> list[dict]:
    """Cross-file gate: every `request.transport_ref` an endpoint declares must
    name a transport the sibling connector.json declares in `transports`.

    This is the cross-file half of the contract's §Transport Selection rule.
    `ConnectorBase._transport_refs_resolvable` already enforces it for every
    connector-INTERNAL ref site (auth ops, post-auth requests, resource
    discovery), but an endpoint lives in its own document, so no single-document
    model validator can see both sides — only a connector-anchored walk can.
    The wording mirrors that model validator's so one rule reads the same
    wherever it fires.

    A non-string ref is left alone (the model already reports the type error),
    and a `transports` value that is not a dict means the connector itself is
    malformed — its own model error stands, and fabricating "declared: []"
    findings on top of it would only bury it."""
    if not isinstance(ep_doc, dict) or not isinstance(transports, dict):
        return []
    findings: list[dict] = []
    for pointer, ref in _api_operation_transport_refs(ep_doc):
        if not isinstance(ref, str) or ref in transports:
            continue
        findings.append(finding(
            rule="RULE-ENDP-047",
            message_id="transport-ref-undeclared", kind="fail", path=pointer,
            message=(
                f"{pointer} transport_ref={ref!r} is not declared in the sibling "
                f"connector.json `transports` (declared: {sorted(transports)!r}; "
                "spec: §Transport Selection). A request dispatches only through a "
                "transport the connector declares.")))
    return findings


def _endpoint_locator_findings(ep_doc: Any) -> list[dict]:
    """Gate: an API `endpoint_id` must equal the handle derived from its resource
    locator — the read `request.path` when present, else the first write path
    (the contract's `resources[].key` derivation rule). Out of scope for
    this path-based check: database endpoints — their ids are gated separately in
    `_database_endpoint_locator_findings` over the verbatim `database_object`.

    The derivation is a plain flatten (lowercase, `__` between levels, path-params
    dropped); the authoring contract assumes charset-safe paths. A path that
    flattens to an empty handle (all path-params) or a non-charset-safe one
    (a `.json` suffix, a dotted `/v1.0/` version) has NO derivable `endpoint_id`,
    so the id-derivation invariant is unsatisfiable — that is an ERROR against the
    path (not a fabricated `must equal …` id): a gate must reject it, not wave it
    through, or an author could decouple the id from its resource by adding a `.`."""
    if not isinstance(ep_doc, dict):
        return []
    endpoint_id = ep_doc.get("endpoint_id")
    if not isinstance(endpoint_id, str) or not endpoint_id:
        return []  # a missing/invalid id is the model's job, not this check's
    paths = _api_operation_paths(ep_doc)  # read emitted first = the canonical locator
    if not paths:
        return []
    pointer, path = paths[0]
    handle = _flatten_api_locator(path)
    if not handle or not SLUG_RE.match(handle):
        return [finding(
            rule="RULE-ENDP-046",
            message_id="locator-unstable", kind="fail", path=pointer,
            message=(
                f"cannot derive a stable endpoint_id from request.path {path!r} — it "
                f"flattens to {handle!r}, which is empty or carries characters outside "
                f"the id charset ({SLUG_RE.pattern}). The derivation assumes charset-"
                "safe, non-empty paths; rename the path (e.g. drop a '.json' suffix) or "
                "extend the derivation rule with sanitization."))]
    if handle != endpoint_id:
        return [finding(
            rule="RULE-ENDP-046",
            message_id="locator-mismatch", kind="fail", path="/endpoint_id",
            message=(
                f"endpoint_id {endpoint_id!r} must equal {handle!r} — the handle derived from "
                f"request.path {path!r} (lowercase, '__' between path levels, path-params "
                "dropped) — so distinct paths like /v1/x and /v2/x get distinct ids."))]
    return []


def _keyset_initial_null_findings(ep_doc: Any) -> list[dict]:
    """Gate for RULE-ENDP-044, reached from both api-endpoint routes.

    Everything the rule asks and why is the record's; this walks down to the
    `keyset` block by `isinstance` at every step, so a malformed document
    falls through here rather than crashing on behalf of a rule it cannot
    grade."""
    if not isinstance(ep_doc, dict):
        return []
    operations = ep_doc.get("operations")
    if not isinstance(operations, dict):
        return []
    read = operations.get("read")
    if not isinstance(read, dict):
        return []
    pagination = read.get("pagination")
    if not isinstance(pagination, dict) or pagination.get("type") != "keyset":
        return []
    keyset = pagination.get("keyset")
    if not isinstance(keyset, dict) or "initial" not in keyset:
        return []
    if keyset["initial"] is None:
        return [finding(
            rule="RULE-ENDP-044",
            message_id="keyset-initial-explicit-null", kind="fail",
            path="/operations/read/pagination/keyset/initial",
            message=(
                "operations.read.pagination.keyset.initial is explicitly null; "
                "omit the field to say there is no first-page key, rather than "
                "spelling that absence as a null value."))]
    return []


# --- Database endpoint id gate ---------------------------------------------------
# A database `endpoint_id` is a derived handle over the verbatim `database_object`,
# NOT authored freely: `slug(schema)__slug(table)[__slug(catalog)]__<hash8>`. The
# derivation is the single source of truth in `analitiq.contracts.endpoint_identity`
# (imported above) — the same module the discovery path mints
# through, so validator and producer share ONE derivation and cannot drift. The
# verbatim identity lives in `database_object`; the handle is never decoded back.

def _database_endpoint_locator_findings(ep_doc: Any) -> list[dict]:
    """Gate: a database `endpoint_id` must equal the slug+hash handle derived from
    its verbatim `database_object` (`analitiq.contracts.endpoint_identity`)."""
    if not isinstance(ep_doc, dict):
        return []
    endpoint_id = ep_doc.get("endpoint_id")
    dbo = ep_doc.get("database_object")
    if not isinstance(endpoint_id, str) or not endpoint_id or not isinstance(dbo, dict):
        return []  # missing pieces are the model's job, not this check's
    name = dbo.get("name")
    if not isinstance(name, str) or not name:
        return []  # required name absent -> the model reports it
    schema = dbo.get("schema") if isinstance(dbo.get("schema"), str) else None
    catalog = dbo.get("catalog") if isinstance(dbo.get("catalog"), str) else None
    expected = derive_db_endpoint_id(catalog, schema, name)
    if endpoint_id != expected:
        return [finding(
            rule="RULE-DBEP-011",
            message_id="db-locator-mismatch", kind="fail", path="/endpoint_id",
            message=(
                f"endpoint_id {endpoint_id!r} must equal {expected!r} — the handle derived "
                "from database_object (slug(schema)__slug(table)[__slug(catalog)]__hash8, "
                "per analitiq.contracts.endpoint_identity). The verbatim identity stays in "
                "database_object; the id is a derived handle, never parsed back."))]
    return []


def endpoint_filename_findings(ep_doc: Any, filename: str) -> list[dict]:
    """Public gate: an endpoint file must be named `{endpoint_id}.json`.

    Returns findings citing RULE-PKG-031: `kind: "fail"`
    (carrying `severity: "error"`) when `filename` disagrees with the doc's
    `endpoint_id`, `kind: "notApplicable"` (no `severity`) when the id is
    missing/unusable, empty when they agree. Exported so a filesystem-walking
    consumer that assembles a pipeline bundle (and so cannot reach the gate
    through `validate_document`, whose bundle entry point takes filename-less
    in-memory docs) enforces the invariant through this one shared
    implementation instead of duplicating it. Pair with
    `is_stem_addressed_endpoint_path` to apply the gate on the same layout
    condition the validator uses."""
    if not isinstance(ep_doc, dict):
        return []
    endpoint_id = ep_doc.get("endpoint_id")
    if not isinstance(endpoint_id, str) or not endpoint_id:
        # notApplicable, not fail: the check knows exactly which rule it would
        # be grading (RULE-PKG-031) — it just has no usable endpoint_id to
        # compare the filename against this time.
        return [finding(
            rule="RULE-PKG-031",
            message_id="filename-check-skipped-no-id", kind="notApplicable",
            path="/endpoint_id",
            message=(
                f"endpoint file {filename!r} has no usable string endpoint_id; "
                "cannot verify the filename matches."),
        )]
    expected = f"{endpoint_id}.json"
    if filename != expected:
        return [finding(
            rule="RULE-PKG-031",
            message_id="filename-id-mismatch", kind="fail", path="/endpoint_id",
            message=(
                f"endpoint file is named {filename!r} but endpoint_id is {endpoint_id!r}; "
                f"it must be named {expected!r} (the engine locates endpoints/{{endpoint_id}}.json)."),
        )]
    return []


def _api_endpoint_document_findings(
        ep_doc: Any, transports: Any, *, filename: str = "") -> list[dict]:
    """The checks a single api-endpoint document must pass on its own, given
    the connector `transports` its `transport_ref` sites resolve against.

    Defined once and called from both `check_coverage`'s sibling-endpoint loop
    and the standalone `_validate_api_endpoint` route: a document's findings are
    a property of the document, not of the call that reached it, and two
    independently maintained call lists cannot hold that. What stays outside
    this function is route-specific: `check_coverage`'s
    cross-sibling duplicate-`endpoint_id` and native/arrow-type coverage
    checks, and `_validate_api_endpoint`'s resolution of `transports` from a
    sibling `connector.json` it does not already have in hand."""
    findings = _model_findings(ep_doc, _API_ENDPOINT_ADAPTER)
    findings.extend(_endpoint_locator_findings(ep_doc))
    if filename:
        findings.extend(endpoint_filename_findings(ep_doc, filename))
    if isinstance(ep_doc, dict):
        findings.extend(_embedded_schema_findings(ep_doc))
        findings.extend(_keyset_initial_null_findings(ep_doc))
        findings.extend(_run_guarded(_embedded_schema_example_findings, ep_doc,
                                     crash_label="embedded schema example grading",
                                     rule="RULE-ENDP-063"))
        findings.extend(_endpoint_transport_ref_findings(ep_doc, transports))
    return findings


def is_stem_addressed_endpoint_path(doc_path: PurePath) -> bool:
    """True iff `doc_path` is an authored connection-scoped endpoint file the engine
    locates by its filename stem — `.../definition/endpoints/{endpoint_id}.json`.

    Database endpoints have two on-disk shapes and only one is stem-addressed. The
    other is the hash-addressed snapshot `.../endpoints/{endpoint_id}/schemas/
    {schema_hash}.json`, whose basename is a content hash by design — the
    filename↔id gate must NOT fire there. The parent directory (`endpoints` under
    `definition` vs `schemas`) is the discriminator, so a snapshot is left
    unchecked while the authored file the engine resolves by stem is gated."""
    parent = doc_path.parent
    return parent.name == "endpoints" and parent.parent.name == "definition"


def is_addressed_endpoint_path(doc_path: PurePath) -> bool:
    """True iff `doc_path` is an endpoint file sitting at the home the engine
    resolves it from — an `endpoints/` directory, whatever carries that
    directory: a connector release for an api endpoint, a connection's
    `definition/` for a connection-scoped one.

    Wider than `is_stem_addressed_endpoint_path`, which additionally demands
    `definition/` because a DATABASE endpoint has a second on-disk shape whose
    basename is a content hash. An api endpoint has only the one shape, so the
    `endpoints/` parent is the whole question. What both exclude is a bare or
    staged path: a file not yet at its home carries no filename the engine will
    ever resolve, so RULE-PKG-031 has nothing to grade there and reporting it
    would fire on every pass of an authoring fix loop."""
    return doc_path.parent.name == "endpoints"


class _Operation(Enum):
    """What a check asked of the package's tree. Each value is what an author
    changes so the kernel grants it where the kernel refused the permission."""

    LOOKUP = "Make every directory on the path to it searchable"
    LISTING = "Make it readable, and every directory on the path to it searchable"
    READ = "Make it readable"
    WALK = ("Make it and every directory below it readable and searchable, and every "
            "directory on the path to it searchable")


@dataclass(frozen=True)
class _Failed:
    """`operation` failed, which says nothing about what is there: a check
    reading its answer is withheld, never graded as if nothing were. Only a
    refused permission is the author's to grant, so only it names a remedy."""

    operation: _Operation
    error: OSError

    @property
    def why(self) -> str:
        asked = self.operation.name.lower()
        if isinstance(self.error, PermissionError):
            return (f"could not be opened: the {asked} was refused ({self.error}). "
                    f"{self.operation.value}, then re-run")
        return f"could not be opened: the {asked} failed ({self.error})"


_Answer = TypeVar("_Answer")


def _asked(operation: _Operation, question: Callable[[], _Answer]) -> _Answer | _Failed:
    """The tree's answer to `question`, or the failure it raised instead.

    Every lookup, listing and read a check makes beside the document goes
    through here, so no failed one is read as absence and none escapes a check.
    """
    try:
        return question()
    except OSError as error:
        return _Failed(operation, error)


@dataclass(frozen=True)
class _Read:
    """A sibling's document: whatever the file held, `null` among them."""

    doc: Any


@dataclass(frozen=True)
class _Unparseable:
    error: ValueError | RecursionError

    @property
    def why(self) -> str:
        return f"could not be read or parsed ({self.error})"


class _NotAFile:
    why = "is not a regular file; nothing was read from it"


_NOT_A_FILE = _NotAFile()

_Sibling = _Read | _NotAFile | _Failed | _Unparseable


def _read_sibling(path: Location) -> _Sibling:
    """What the sibling JSON document at `path` turned out to be."""
    is_file = _asked(_Operation.LOOKUP, path.is_file)
    if isinstance(is_file, _Failed):
        return is_file
    if not is_file:
        # Only a regular file is read. A directory raises with an errno that
        # says nothing about the package; a FIFO waits for a writer that never
        # comes, and the check never returns at all.
        return _NOT_A_FILE
    try:
        text = _asked(_Operation.READ, path.read_text)
        return text if isinstance(text, _Failed) else _Read(json.loads(text))
    except _JSON_TEXT_REFUSALS as error:
        return _Unparseable(error)


def _unread(path: Location, sibling: _NotAFile | _Failed | _Unparseable) -> str:
    return f"sibling {path.name} {sibling.why}."


def _load_json_sibling(path: Location, *, message_id: str) -> tuple[_Sibling, list[dict]]:
    """Read a sibling JSON document, failing without a rule when no document
    was read: which rule went unchecked is the caller's to name."""
    sibling = _read_sibling(path)
    if isinstance(sibling, _Read):
        return sibling, []
    return sibling, [finding(
        rule=None, message_id=message_id, kind="fail", path="", message=_unread(path, sibling))]


def type_map_findings(
    doc: Any, scope: Literal["connector", "connection"] = "connector",
) -> list[dict]:
    """Validate a type-map document: model errors + advisory rule warnings for
    each direction's section + (write-vocabulary coverage). `doc` is nominally
    the whole `{$schema, read, write}` object — a malformed map can hand it any
    JSON-parseable value instead, which `_model_findings` rejects — and each
    advisory reads only the section of its own direction, because a rule's
    matcher is `native_type` under `read` and `arrow_type` under `write`.

    `scope` decides the write vocabulary alone: a connector write section must
    render all of it, a connection map is gap-only (`RULE-TMAP-018`) and would
    earn the write-vocabulary finding (`RULE-TMAP-017`) forever."""
    # An unsupported value would silently select the scope nobody asked for. It
    # is the caller's own argument rather than anything the document did, so it
    # raises past the guard instead of arriving as a finding.
    if scope not in ("connector", "connection"):
        raise ValueError(f"scope must be 'connector' or 'connection', got {scope!r}")
    return _graded_type_map(doc, renders_write_vocabulary=scope == "connector")


def _graded_type_map(doc: Any, *, renders_write_vocabulary: bool) -> list[dict]:
    return _run_guarded(_type_map_document_findings, doc, renders_write_vocabulary,
                        crash_label="type-map grading")


def _type_map_document_findings(doc: Any, renders_write_vocabulary: bool) -> list[dict]:
    findings = _model_findings(doc, _TYPE_MAP_ADAPTER)
    if not isinstance(doc, dict):
        return findings
    for direction in TYPE_MAP_DIRECTIONS:
        findings.extend(_type_map_rule_warnings(doc.get(direction), direction))
    write = doc.get("write")
    if renders_write_vocabulary and isinstance(write, list):
        findings.extend(_write_vocabulary_findings(write))
    return findings


def _about(sibling: Location, findings: list[dict], *, seen_from: Location) -> list[dict]:
    """`findings`, graded on the document at `sibling`, reported beside the
    document at `seen_from`.

    Each locates itself with a pointer into `sibling`, which beside another
    document reads as a node of that one.
    """
    at = reference(sibling, seen_from=seen_from)
    return [qualified(f, at) for f in findings]


def _stray_type_map_paths(parent: Location) -> list[Location]:
    """The entries in `parent` carrying a type-map name that is not the map's.

    The name is what is refused, so a directory or a broken link carrying it
    counts: answering only for a regular file would let an author keep the name
    by making it something else. Read off the listing, which names every entry
    without looking any of them up. Sorted because a tree lists its entries in
    no particular order, and the same directory has to report its strays in the
    same order on every listing.
    """
    return sorted(parent.glob(_STRAY_TYPE_MAP_GLOB))


@dataclass(frozen=True)
class TypeMapLoad:
    """The type map one `definition/` directory holds.

    `document` is what its `type-map.json` held — `None` when that file held
    JSON `null` — and `loaded` says whether anything was read at all. Asking for
    the document when nothing was read raises `LookupError`: there is no stand-in
    that grades as a file that is not there. Absence is no finding: whether a
    directory must hold a map is its caller's to say. `sections` is the
    directions the document carries a section for — present and not `null`,
    whether or not what is there is a valid rule list: a malformed section is
    the model's to report, never a section the directory lacks.

    `findings` pairs each finding with the path of the entry it concerns,
    relative to the directory and `.` for the directory itself, so a caller
    holding the directory at a different site roots it by joining that path onto
    the site.

    `unread` names the map where its lookup or read failed, and `.` where the
    listing of the directory failed. What the map holds is then unknown, so
    while `unread` names anything no direction can be said to lack a section.
    """

    _read: _Read | None
    findings: list[tuple[str, dict]]
    unread: list[str]

    @property
    def loaded(self) -> bool:
        return self._read is not None

    @property
    def document(self) -> Any:
        if self._read is None:
            raise LookupError(f"no {TYPE_MAP_FILENAME} was read")
        return self._read.doc

    @property
    def sections(self) -> frozenset[str]:
        doc = self._read.doc if self._read is not None else None
        if not isinstance(doc, dict):
            return frozenset()
        return frozenset(d for d in TYPE_MAP_DIRECTIONS if doc.get(d) is not None)


def load_type_map(parent: Path | Location, *, rule: str | None) -> TypeMapLoad:
    """Load the type map in `parent`, refusing every other type-map name there.

    Published because a connector's map and a connection's are loaded by one
    rule, and any second implementation of it answers differently at the
    edges. `rule` is what the loader's own findings cite: the record binding the
    directory being loaded, which differs by scope, so it is the caller's to
    name — `None` where no record binds it.

    A stray name is refused whatever sits under it — a directory or a broken
    link included — because the name is what reads as a type map.

    A `parent` that `located` refuses raises its refusal.
    """
    parent = located(parent)
    listed = _asked(_Operation.LISTING, lambda: (
        any(True for _ in parent.glob(TYPE_MAP_FILENAME)), _stray_type_map_paths(parent)))
    if isinstance(listed, _Failed):
        return TypeMapLoad(None, [(".", finding(
            rule=rule,
            message_id="type-map-dir-unlisted", kind="notApplicable", path="",
            message=f"type map not loaded: the directory {listed.why}."))], unread=["."])
    listed_map, strays = listed
    read: _Read | None = None
    findings: list[tuple[str, dict]] = []
    unread: list[str] = []
    if listed_map:
        path = parent / TYPE_MAP_FILENAME
        sibling = _read_sibling(path)
        if isinstance(sibling, _Read):
            read = sibling
        elif isinstance(sibling, _Failed):
            unread.append(TYPE_MAP_FILENAME)
            findings.append((TYPE_MAP_FILENAME, finding(
                rule=rule, message_id="type-map-unreadable", kind="notApplicable", path="",
                message=_unread(path, sibling))))
        else:
            findings.append((TYPE_MAP_FILENAME, finding(
                rule=rule, message_id="type-map-unparseable", kind="fail", path="",
                message=_unread(path, sibling))))
    for stray in strays:
        findings.append((stray.name, finding(
            rule=rule,
            message_id="stray-type-map-document", kind="fail", path="",
            message=(
                f"sibling {stray.name} is not read as a type map: one "
                f"{TYPE_MAP_FILENAME} carries the rules for every direction the map covers, "
                f"so nothing grades what {stray.name} holds."))))
    return TypeMapLoad(read, findings, unread)


def _missing_section(kind: str, direction: str, what: str, load: TypeMapLoad) -> dict:
    """The RULE-PKG-030 finding for a `direction` the connector's kind needs
    and its map does not carry. It says whether the map was read at all — absent,
    or present but not loadable, which its own finding reports — or was read and
    lacks the section, because those ask for different edits."""
    reason = (f"{TYPE_MAP_FILENAME} carries no {direction!r} section"
              if load.loaded else f"no {TYPE_MAP_FILENAME} was read")
    return finding(
        rule="RULE-PKG-030",
        message_id=f"{direction}-map-missing", kind="fail", path="",
        message=(f"{kind} connector requires a sibling {TYPE_MAP_FILENAME} carrying a "
                 f"{direction!r} rule list ({what}); {reason}."))


def _coverage_skipped(*, message_id: str, path: str, why: str) -> list[dict]:
    """The one shape a coverage skip takes: a `notApplicable` naming the question
    that went unasked, rather than a clean pass on one never put. No rule is
    named — which of the coverage rules would have applied is itself what the
    skipped reading would have decided."""
    return [finding(message_id=message_id, kind="notApplicable", path=path,
                    message=f"type-map coverage skipped: {why}")]


def check_coverage(doc: dict, doc_path: Path | Location | None) -> list[dict]:
    """Connector ↔ sibling type-map coverage (the irreducibly cross-file check).

    A connector's `doc_path` that `located` refuses raises its refusal.
    """
    if not isinstance(doc, dict):
        # No rule to name, for the reason the no-path branch below carries:
        # every coverage rule reads a key off the document to decide what its
        # package must hold, so a document that is not a mapping settles none
        # of them.
        return _coverage_skipped(message_id="coverage-check-skipped-not-a-mapping", path="",
                                 why="the document is not a JSON object.")
    if doc_path is None:
        # No rule to name: PKG-030/032/033/035 are each about a sibling file or
        # directory this function reads beside the document, so without a
        # location none of them can be checked, let alone singled out.
        return _coverage_skipped(message_id="coverage-check-skipped-no-path", path="",
                                 why="no filesystem-anchored document path.")
    kind = doc.get("kind")
    if kind not in ("api", *_DATABASE_KINDS, *_STORAGE_KINDS):
        return _coverage_skipped(
            message_id="coverage-check-skipped-bad-kind", path="/kind",
            why=f"connector 'kind'={kind!r} is not in the closed enum "
                "(the model enforces this).")

    anchor = located(doc_path)
    package = anchor.parent
    type_map = load_type_map(package, rule="RULE-PKG-030")
    # `.` is the directory itself, which is the connector's package.
    findings = [f if entry == "." else _about(package / entry, [f], seen_from=anchor)[0]
                for entry, f in type_map.findings]
    map_path = package / TYPE_MAP_FILENAME
    if type_map.loaded:
        # An api connector's write section is refused whole below, so holding
        # it to the write vocabulary would ask for rules the same pass removes.
        findings.extend(_about(map_path, _graded_type_map(
            type_map.document, renders_write_vocabulary=kind != "api"), seen_from=anchor))
    if kind in _STORAGE_KINDS:
        return findings

    sections = type_map.sections
    # While the map went unread, what it carries is unknown: no section is missing.
    if "read" not in sections and not type_map.unread:
        findings.append(_missing_section(kind, "read", "native → Arrow", type_map))
    if kind in _DATABASE_KINDS:
        if "write" not in sections and not type_map.unread:
            findings.append(_missing_section(kind, "write", "Arrow → native DDL", type_map))
        return findings

    # api: no write section, and every endpoint's natives must be covered by the read rules.
    if "write" in sections:
        findings.extend(_about(map_path, [finding(
            rule="RULE-PKG-030",
            message_id="write-map-not-allowed", kind="fail", path="/write",
            message=(
                f"api connector's {TYPE_MAP_FILENAME} must not carry a 'write' rule "
                "list; an api connector has no write direction."))], seen_from=anchor))
    # Read rules that cannot be rendered from are carried forward rather than
    # returned on: returning here would withhold every endpoint-anchored check
    # too, hiding every defect in every endpoint document behind one broken
    # file. What is carried need not be a list, so the readers below ask that.
    read_rules = type_map.document["read"] if "read" in sections else None
    if not isinstance(read_rules, list):
        # notApplicable, not fail: the check knows exactly which rule it would
        # be grading (RULE-PKG-033) — the read rules themselves are missing,
        # unreadable, or malformed, which is already reported above under its own
        # rule; this
        # says the coverage question specifically was never answered.
        findings.append(finding(
            rule="RULE-PKG-033",
            message_id="native-type-coverage-skipped", kind="notApplicable", path="",
            message=(
                "native_type coverage against the read rules was not rendered: no "
                f"{TYPE_MAP_FILENAME} was read, or its 'read' section is absent or not "
                "a list. Endpoint native_type/arrow_type agreement is unverified until "
                "it is fixed.")))
    endpoint_dir = package / "endpoints"
    present = _asked(_Operation.LOOKUP, endpoint_dir.is_dir)
    if present is False:
        findings.append(finding(
            rule="RULE-PKG-035",
            message_id="endpoints-dir-missing", kind="fail", path="",
            message="api connector requires a sibling 'endpoints/' directory; missing."))
        return findings
    # Scan recursively: every *.json the walk reaches under endpoints/ must sit
    # at exactly `endpoints/{endpoint_id}.json` (flat), so a nested or misplaced
    # file is flagged rather than reported as a false pass.
    endpoint_files = present if isinstance(present, _Failed) else _asked(
        _Operation.WALK, lambda: sorted(endpoint_dir.rglob("*.json")))
    if isinstance(endpoint_files, _Failed):
        findings.append(finding(
            rule="RULE-PKG-031",
            message_id="endpoints-dir-unlisted", kind="notApplicable", path="",
            message=f"endpoint documents not checked: 'endpoints/' {endpoint_files.why}."))
        return findings
    if not endpoint_files:
        findings.append(finding(
            rule="RULE-PKG-035",
            message_id="endpoints-dir-empty", kind="fail", path="",
            message="api connector's 'endpoints/' directory has no *.json files."))
        return findings
    # Cross-endpoint identity: `endpoint_id` is unique within the connector
    # release (the contract's shared-metadata rules). The
    # filename==id rule only makes IDENTICAL ids collide on the filesystem (and
    # then surfaces obliquely as a filename mismatch); enforce the invariant
    # directly so a duplicate is reported as a duplicate.
    seen_ids: dict[str, str] = {}
    for ep_path in endpoint_files:
        rel = ep_path.key.relative_to(endpoint_dir.key).as_posix()
        if "/" in rel:
            findings.extend(_about(ep_path, [finding(
                rule="RULE-PKG-031",
                message_id="endpoint-file-nested", kind="fail", path="",
                message=(
                    f"endpoint file 'endpoints/{rel}' is nested; endpoints must be flat "
                    "at 'endpoints/{endpoint_id}.json' (the engine resolves them by id)."))],
                seen_from=anchor))
            continue
        endpoint, load = _load_json_sibling(ep_path, message_id="endpoint-file-unreadable")
        if not isinstance(endpoint, _Read):
            findings.extend(_about(ep_path, load, seen_from=anchor))
            continue
        ep_doc = endpoint.doc
        # Each sibling endpoint is a full api-endpoint document — validate it
        # with the checks shared with the standalone single-document route.
        endpoint_findings = _api_endpoint_document_findings(
            ep_doc, doc.get("transports"), filename=ep_path.name)
        ep_id = ep_doc.get("endpoint_id") if isinstance(ep_doc, dict) else None
        if isinstance(ep_id, str) and ep_id:
            if ep_id in seen_ids:
                # The first declaration is named here because it is half of
                # the complaint; `path` names the second.
                endpoint_findings.append(finding(
                    rule="RULE-PKG-032",
                    message_id="duplicate-endpoint-id", kind="fail", path="/endpoint_id",
                    message=(
                        f"duplicate endpoint_id {ep_id!r}: already declared by "
                        f"'endpoints/{seen_ids[ep_id]}'; endpoint_id must be unique "
                        "within the connector release.")))
            else:
                seen_ids[ep_id] = ep_path.name
        # A JSON array/string endpoint file is already a recorded model error;
        # the coverage walk calls `.get()` and would crash, replacing the
        # actionable findings with a generic "validator bug" via _run_guarded.
        # The rendering is the one check here that needs the read rules; rules
        # that did not load skip it, and the notApplicable finding raised beside
        # the connector says the coverage question went unanswered.
        if isinstance(ep_doc, dict) and isinstance(read_rules, list):
            for native, arrow, pointer in _collect_native_arrow_pairs(ep_doc):
                rendered = _render_arrow_type(native, read_rules)
                if rendered is None:
                    endpoint_findings.append(finding(
                        rule="RULE-PKG-033",
                        message_id="native-type-unresolved", kind="fail", path=pointer,
                        message=(
                            f"native_type {native!r} has no matching rule in the 'read' "
                            f"section of sibling {TYPE_MAP_FILENAME}.")))
                elif not _arrow_type_eq(rendered, arrow) and not (rendered == "Json" and arrow in _NARROWING_ARROW_TYPES):
                    endpoint_findings.append(finding(
                        rule="RULE-PKG-033",
                        message_id="native-type-arrow-mismatch", kind="fail", path=pointer,
                        message=(
                            f"native_type {native!r} resolves to {rendered!r} via the "
                            f"'read' section of {TYPE_MAP_FILENAME} but the endpoint "
                            f"declares arrow_type={arrow!r}.")))
        findings.extend(_about(ep_path, endpoint_findings, seen_from=anchor))
    return findings


# ---------------------------------------------------------------------------
# Adapters (built once)
# ---------------------------------------------------------------------------

_CONNECTOR_ADAPTER = TypeAdapter(Connector)
_API_ENDPOINT_ADAPTER = TypeAdapter(ApiEndpointDoc)
_DATABASE_ENDPOINT_ADAPTER = TypeAdapter(DatabaseEndpointDoc)
_TYPE_MAP_ADAPTER = TypeAdapter(TypeMapDoc)


# ---------------------------------------------------------------------------
# Per-kind validators + registration
# ---------------------------------------------------------------------------

def _validate_connector(doc: Any, location: Location | None) -> list[dict]:
    findings = model_and_schema_findings(doc, _CONNECTOR_ADAPTER)
    findings += check_coverage(doc, location)
    return findings


def _validate_api_endpoint(doc: Any, location: Location | None) -> list[dict]:
    transports: Any = None
    sibling_findings: list[dict] = []
    addressed = location is not None and is_addressed_endpoint_path(location.key)
    if isinstance(doc, dict):
        # RULE-ENDP-047 is cross-document: it needs the sibling connector.json's
        # `transports`. Where that cannot be read, say so rather than
        # returning a silent clean pass — an author validating a single
        # endpoint file would otherwise read `passed: true` as "the
        # transport_ref is fine", which is reassurance the check never earned.
        # notApplicable, not fail, matching `endpoint_filename_findings`'s
        # convention for a check it cannot perform from the given path.
        declared_refs = sorted({
            ref for _, ref in _api_operation_transport_refs(doc) if isinstance(ref, str)
        })
        if declared_refs:
            # Look up the sibling connector when the layout gives us one:
            # `endpoints/{id}.json` sits one level below `connector.json`. The
            # connector-builder skill validates each endpoint on its own, so a
            # blind warning here would fire on every pass of its fix loop and
            # could never be cleared — an alarm that cannot be acted on trains
            # authors to ignore the id. Outside that layout no connector is
            # read: a connector two levels up is not this endpoint's.
            sibling = location.parent.parent / "connector.json" if addressed else None
            connector: _Sibling = _NOT_A_FILE
            if sibling is not None:
                # A failed read has not evaluated RULE-ENDP-047 one way or the
                # other; the branch below names it, as notApplicable.
                connector, load_findings = _load_json_sibling(
                    sibling, message_id="sibling-connector-unreadable")
                # No regular file there is not a failure to read one: an
                # endpoint validated before its connector exists has none.
                if connector is not _NOT_A_FILE:
                    sibling_findings.extend(_about(sibling, load_findings, seen_from=location))
            connector_doc = connector.doc if isinstance(connector, _Read) else None
            transports = connector_doc.get("transports") if isinstance(connector_doc, dict) else None
            # `transports` resolved: RULE-ENDP-047 is graded against it by
            # `_api_endpoint_document_findings` below. What is left to report
            # here is the cases where it could not be resolved, each naming
            # which one happened.
            if not isinstance(transports, dict):
                if isinstance(connector, _Read):
                    # Connector found, but its `transports` is missing or not an
                    # object. `_endpoint_transport_ref_findings` returns [] there —
                    # correct at the CONNECTOR-anchored call site, where the
                    # connector's own model error already stands. Here the connector
                    # model never runs, so returning [] would report a clean pass on
                    # an endpoint whose `transport_ref` resolves to nothing. Say what
                    # could not be checked and why. notApplicable, not fail: the
                    # check knows exactly which rule it would grade (RULE-ENDP-047).
                    sibling_findings.append(finding(
                        rule="RULE-ENDP-047",
                        message_id="transport-ref-check-skipped-no-transports",
                        kind="notApplicable", path="",
                        message=(
                            f"transport_ref {declared_refs!r} not checked: the sibling "
                            "connector.json was read but declares no usable `transports` "
                            "object, so there was nothing to resolve the name against. "
                            "Validate the connector to see why.")))
                elif isinstance(connector, _Failed):
                    # Neither absent nor unparseable: the connector may be there
                    # and sound, and either remedy would send the author astray.
                    sibling_findings.append(finding(
                        rule="RULE-ENDP-047",
                        message_id="transport-ref-check-skipped-sibling-unreadable",
                        kind="notApplicable", path="",
                        message=(
                            f"transport_ref {declared_refs!r} not checked: the sibling "
                            f"connector.json at {sibling} could not be opened, so its "
                            "`transports` could not be read. Fix the error reported "
                            "above and re-run.")))
                elif isinstance(connector, _Unparseable):
                    # The file IS there and did not parse. Reporting it as absent
                    # would contradict the parse finding emitted beside it.
                    sibling_findings.append(finding(
                        rule="RULE-ENDP-047",
                        message_id="transport-ref-check-skipped-unparseable",
                        kind="notApplicable", path="",
                        message=(
                            f"transport_ref {declared_refs!r} not checked: the sibling "
                            f"connector.json at {sibling} could not be parsed, so its "
                            "`transports` could not be read. Fix the error reported "
                            "above and re-run.")))
                else:
                    if location is None:
                        reason = ("no path was given, so there is no directory to "
                                  "read the connector from. Validate it at its path, "
                                  "`endpoints/{endpoint_id}.json` beside its connector")
                    elif addressed:
                        reason = ("there is no connector.json file beside the "
                                  "`endpoints/` directory holding this document. "
                                  "Place the connector there and re-run")
                    else:
                        reason = ("the document is not directly inside an "
                                  "`endpoints/` directory, so no connector is read "
                                  "for it. Place it at `endpoints/{endpoint_id}.json` "
                                  "beside its connector")
                    sibling_findings.append(finding(
                        rule="RULE-ENDP-047",
                        message_id="transport-ref-check-skipped-no-sibling",
                        kind="notApplicable", path="",
                        message=f"transport_ref {declared_refs!r} not checked: {reason}."))
    # Each api-endpoint document goes through the checks shared with
    # `check_coverage`'s sibling-endpoint loop; `transports` is not a dict
    # wherever the branches above could not resolve it, and RULE-ENDP-047 stays silent
    # there rather than reporting on an unresolved comparison. Silence is the
    # whole answer only when the document declares no `transport_ref` at all —
    # every other way of arriving here with `transports` unresolved has already
    # appended the `notApplicable` naming which one it was. The filename is
    # graded only where the engine resolves the document by it.
    findings = _api_endpoint_document_findings(
        doc, transports,
        filename=location.name if addressed else "")
    findings.extend(sibling_findings)
    return findings


def _validate_database_endpoint(doc: Any, location: Location | None) -> list[dict]:
    # The filename↔id gate applies only to the authored connection-scoped file the
    # engine locates by stem (`.../definition/endpoints/{endpoint_id}.json`), not to
    # the hash-addressed materialized snapshot (`.../endpoints/{endpoint_id}/schemas/
    # {schema_hash}.json`), whose basename is a content hash by design. Mirrors the
    # api-endpoint path, reusing the one shared `endpoint_filename_findings` so the
    # invariant is defined once — but gated on the layout, since a bare/staged path
    # not yet at its final home carries no filename to check. The id itself is always
    # gated against database_object regardless of location.
    findings = _model_findings(doc, _DATABASE_ENDPOINT_ADAPTER)
    findings += _database_endpoint_locator_findings(doc)
    if location is not None and is_stem_addressed_endpoint_path(location.key):
        findings += endpoint_filename_findings(doc, location.name)
    return findings


def _validate_type_map(doc: Any, location: Location | None) -> list[dict]:  # skipcq: PYL-W0613 — uniform registered-validator signature
    return type_map_findings(doc)


register_kind("connector", _validate_connector)
register_kind("api-endpoint", _validate_api_endpoint)
register_kind("database-endpoint", _validate_database_endpoint)
register_kind("type-map", _validate_type_map)
