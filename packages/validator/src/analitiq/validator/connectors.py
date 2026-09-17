"""Connector-package validation — the connector / api-endpoint / database-endpoint
/ type-map artifact kinds.

Single-document validity is delegated to the Pydantic **contract models**
(`analitiq-contract-models`, the same models the published JSON Schemas are
generated from): each document kind is validated with `TypeAdapter(...).
validate_python`, which enforces structure *and* every cross-field rule the
contract defines — offline, no schema fetch, no drift. On top of the models this
module adds only what a single-document model cannot express:

- **cross-file coverage** (`RULE-PKG-030`/`RULE-PKG-033`/`RULE-PKG-035`): a
  connector must ship the right sibling type-map files for its kind, and an API
  connector's read map must cover every `(native_type, arrow_type)` its
  endpoint files declare;
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
  rules, dead uppercase-only read patterns, and write-map vocabulary gaps
  (`RULE-TMAP-014`/`RULE-TMAP-022`/`RULE-TMAP-017`).

At import this module registers its detector→validator pairs with the core
dispatch registry, so `_core` never hard-codes connector branches.
"""
from __future__ import annotations

import json
import re
import reprlib
import sys
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

from ._core import (
    contract_model_domain,
    finding,
    register_kind,
    _bounded,
    _missing_schema_url_findings,
    _model_findings,
    _run_guarded,
)
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
        from analitiq.contracts.type_map import (
            TYPE_MAP_WRITE_SCHEMA_URL, TypeMapReadDoc, TypeMapWriteDoc,
        )
        # Reuse the contract's regex primitives (ECMA named-group + `${name}`
        # placeholder syntax) from the model so the validator's rule-rendering can't
        # drift from the model's rule-validation.
        from analitiq.contracts.type_map import (
            _ECMA_NAMED_BACKREF, _ECMA_NAMED_GROUP, _PLACEHOLDER_RE, _to_python_regex,
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


_READ_MAP_FILENAME = "type-map-read.json"
_WRITE_MAP_FILENAME = "type-map-write.json"
_LEGACY_MAP_FILENAME = "type-map.json"

_CONNECTOR_SENTINELS = ("transports", "connection_contract", "default_transport", "auth")
_STORAGE_KINDS = ("file", "s3", "stdout")
# Database-family kinds own database-endpoint documents and ship both type-map
# directions (read for source, write for destination DDL rendering).
_DATABASE_KINDS = ("database", "nosql", "document")


def is_connector_doc(doc: Any) -> bool:
    return isinstance(doc, dict) and "kind" in doc


def is_api_endpoint_doc(doc: Any) -> bool:
    return isinstance(doc, dict) and "kind" not in doc and "operations" in doc


def is_database_endpoint_doc(doc: Any) -> bool:
    return (
        isinstance(doc, dict)
        and "kind" not in doc
        and "operations" not in doc
        and ("database_object" in doc or "columns" in doc)
    )


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
                m = re.fullmatch(_to_python_regex(matcher_value), probe)
            except re.error:
                continue
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


def _embedded_schema_findings(ep_doc: dict, label: str = "") -> list[dict]:
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
            where = f"{label}{pointer}" if label else pointer
            findings.append(finding(
                rule="RULE-ENDP-048",
                message_id="invalid-embedded-schema", kind="fail", path=pointer,
                message=f"embedded schema at {where} {reason}"))
    return findings


def _embedded_schema_example_findings(ep_doc: dict, label: str = "") -> list[dict]:
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
                    where = _bounded(f"{label}{entry}" if label else entry)
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
        message_id="write-map-missing-family", kind="fail", path="/rules",
        message=(
            f"write map has no rule rendering these Arrow families: {missing}. "
            "If the dialect renders them via a column-type override this is expected; "
            "otherwise add rules so they materialize."),
    )]


def _type_map_rule_warnings(rules: list, direction: str) -> list[dict]:
    """Advisory (non-error) type-map checks the contract tolerates: duplicate
    rules (later ones unreachable) and read patterns that can never match."""
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
                    message_id="duplicate-type-map-rule", kind="fail", path=f"/rules/{i}",
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
            # Strip named groups, named BACKREFERENCES, class/anchor escapes,
            # AND `[...]` character-class contents ([A-Za-z] is a set, not a
            # lowercase literal) before looking for a lowercase literal that can
            # never match an UPPERCASED native. The backref strip must drop
            # `\k<name>` whole: unescaping it leaves the literal `k<name>`, which
            # reads as authored lowercase and warns about a rule that matches
            # perfectly well. Both strips are needed and neither substitutes for
            # the other — dropping backrefs while keeping class contents warns on
            # `^FOO(?<x>[A-Za-z]+)$` instead.
            stripped = _ECMA_NAMED_GROUP.sub("(", matcher)
            stripped = _ECMA_NAMED_BACKREF.sub("", stripped)
            stripped = re.sub(r"\[[^\]]*\]", "", stripped)
            stripped = re.sub(r"\\[dDsSwWbBAZfnrtvux0]", "", stripped)
            if re.search(r"[a-z]", re.sub(r"\\(.)", r"\1", stripped)):
                findings.append(finding(
                    rule="RULE-TMAP-014",
                    message_id="regex-native-case-mismatch", kind="fail",
                    path=f"/rules/{i}/{matcher_key}",
                    message=(
                        f"regex {matcher_key} is matched against UPPERCASED natives; "
                        f"lowercase literals in {matcher!r} can never match."),
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
                out.append((f"/operations/write/{mode}/request/path", block["request"]["path"]))
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
            _collect(block, f"/operations/write/{mode}")
    return out


def _endpoint_transport_ref_findings(ep_doc: Any, transports: Any,
                                     label: str = "") -> list[dict]:
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
        where = f"{label}{pointer}" if label else pointer
        findings.append(finding(
            rule="RULE-ENDP-047",
            message_id="transport-ref-undeclared", kind="fail", path=pointer,
            message=(
                f"{where} transport_ref={ref!r} is not declared in the sibling "
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
        findings.extend(_embedded_schema_findings(ep_doc, label=filename))
        findings.extend(_keyset_initial_null_findings(ep_doc))
        findings.extend(_run_guarded(_embedded_schema_example_findings, ep_doc,
                                     filename, crash_label="embedded schema example grading",
                                     rule="RULE-ENDP-063"))
        findings.extend(_endpoint_transport_ref_findings(ep_doc, transports, label=filename))
    return findings


def is_stem_addressed_endpoint_path(doc_path: Path) -> bool:
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


def is_addressed_endpoint_path(doc_path: Path) -> bool:
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


def _load_json_sibling(
    path: Path, *, rule: str | None, message_id: str,
) -> tuple[Any, list[dict]]:
    """Read a sibling JSON document, reporting a read/parse failure — carrying,
    when the caller names one, the rule that sibling's content would otherwise
    satisfy.

    `rule` is a parameter because the callers are different checks, each
    attributing an unreadable sibling to whichever obligation it was reading
    that sibling to satisfy — RULE-PKG-030 for a type-map load, `None` where
    the read precedes any rule evaluation — rather than a shared default that
    could name the wrong one.
    """
    try:
        return json.loads(path.read_text()), []
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, [finding(
            rule=rule, message_id=message_id, kind="fail", path="/",
            message=f"sibling {path.name} could not be read or parsed ({exc}).")]


def _load_type_map(path: Path) -> tuple[Any | None, list[dict]]:
    """A type-map document, or `None` plus an unparseable-sibling finding."""
    return _load_json_sibling(
        path, rule="RULE-PKG-030", message_id="type-map-unparseable")


def _type_map_rules(doc: Any) -> Any:
    """`doc["rules"]` when `doc` is a dict carrying one, else `None` — the
    defensive read shared by every caller that only needs the rules array and
    must not assume `doc` is the envelope it is nominally supposed to be."""
    return doc.get("rules") if isinstance(doc, dict) else None


def type_map_findings(
    doc: Any,
    direction: Literal["read", "write"],
    scope: Literal["connector", "connection"] = "connector",
) -> list[dict]:
    """Validate a type-map document as the `direction` the CALLER names: model
    errors + advisory rule warnings + (write-vocabulary coverage). The definition
    used wherever a direction is known. `doc` is nominally the whole
    `{$schema, direction, rules}` object — a malformed sibling can hand it any
    JSON-parseable value instead, which `_model_findings` rejects — and the
    advisory/coverage checks only ever needed `rules`.

    Naming the direction IS the assertion: a document declaring the other
    direction fails the model's `direction` Literal alongside every other defect
    it carries, where refusing to grade it at all would report the disagreement
    and nothing else.

    `scope` decides the write vocabulary alone: a connector write map must
    render all of it, a connection map is gap-only (`RULE-TMAP-018`) and would
    earn the write-vocabulary finding (`RULE-TMAP-017`) forever."""
    # An unsupported value would silently select the direction or scope nobody
    # asked for. It is the caller's own argument rather than anything the document
    # did, so it raises past the guard instead of arriving as a finding.
    if direction not in ("read", "write"):
        raise ValueError(f"direction must be 'read' or 'write', got {direction!r}")
    if scope not in ("connector", "connection"):
        raise ValueError(f"scope must be 'connector' or 'connection', got {scope!r}")
    return _run_guarded(_type_map_document_findings, doc, direction, scope,
                        crash_label="type-map grading")


def _type_map_document_findings(doc: Any, direction: str, scope: str) -> list[dict]:
    adapter = _READ_MAP_ADAPTER if direction == "read" else _WRITE_MAP_ADAPTER
    findings = _model_findings(doc, adapter)
    # A document declaring the other direction has its rules keyed for that one —
    # read matches on `native_type`, write on `arrow_type` — so this direction's
    # advisories have nothing to read and report defects the document does not
    # have, burying the disagreement the model states above them. A direction
    # that is missing or unusable disagrees with nothing, and is graded as named.
    declared = doc.get("direction") if isinstance(doc, dict) else None
    if declared in ("read", "write") and declared != direction:
        return findings
    rules = _type_map_rules(doc)
    findings.extend(_type_map_rule_warnings(rules, direction))
    if direction == "write" and scope == "connector" and isinstance(rules, list):
        findings.extend(_write_vocabulary_findings(rules))
    return findings


def check_coverage(doc: dict, doc_path: Path | None) -> list[dict]:
    """Connector ↔ sibling type-map coverage (the irreducibly cross-file check)."""
    if not isinstance(doc, dict) or not any(k in doc for k in _CONNECTOR_SENTINELS):
        return []
    if doc_path is None:
        # No rule to name: PKG-030/032/033/035 are each about a sibling file
        # or directory this function reads by path, so without one none of
        # them can be checked, let alone singled out. A document validated
        # this way can no longer report coverage passed — that question was
        # never asked of it.
        return [finding(
            message_id="coverage-check-skipped-no-path",
            kind="notApplicable", path="/",
            message="type-map coverage skipped: no filesystem-anchored document path.")]
    kind = doc.get("kind")
    if kind not in ("api", *_DATABASE_KINDS, *_STORAGE_KINDS):
        return [finding(
            message_id="coverage-check-skipped-bad-kind",
            kind="notApplicable", path="/kind",
            message=(
                f"type-map coverage skipped: connector 'kind'={kind!r} is not in the "
                "closed enum (the model enforces this)."))]

    findings: list[dict] = []
    parent = doc_path.parent
    read_path, write_path = parent / _READ_MAP_FILENAME, parent / _WRITE_MAP_FILENAME
    if (parent / _LEGACY_MAP_FILENAME).is_file():
        findings.append(finding(
            rule="RULE-PKG-030",
            message_id="legacy-type-map-filename", kind="fail", path="/",
            message=(
                f"sibling {_LEGACY_MAP_FILENAME} is the pre-split name; rename the "
                f"read direction to {_READ_MAP_FILENAME} (and add {_WRITE_MAP_FILENAME} "
                "for database connectors).")))

    if kind in _STORAGE_KINDS:
        for path, direction in ((read_path, "read"), (write_path, "write")):
            if path.is_file():
                doc_, load = _load_type_map(path)
                findings.extend(load)
                if doc_ is not None:
                    findings.extend(type_map_findings(doc_, direction))
        return findings

    # A read map that cannot be rendered from is carried forward rather than
    # returned on: the rendering is what needs it, and returning here would
    # withhold every endpoint-anchored check as well, hiding every defect in
    # every endpoint document behind one broken file. What is carried is
    # whatever `doc.get("rules")` holds when `doc` is a dict — `None` when the
    # file is absent, unreadable, or not a dict, and whatever value the dict's
    # `rules` key holds otherwise, list or not — so the readers below ask
    # whether it is a list rather than whether it is set.
    read_doc: Any = None
    read_rules: Any = None
    if not read_path.is_file():
        findings.append(finding(
            rule="RULE-PKG-030",
            message_id="read-map-missing", kind="fail", path="/",
            message=f"connector requires sibling {_READ_MAP_FILENAME} (native → Arrow); missing."))
    else:
        read_doc, load = _load_type_map(read_path)
        findings.extend(load)
        if read_doc is not None:
            findings.extend(type_map_findings(read_doc, "read"))
            read_rules = _type_map_rules(read_doc)

    if kind in _DATABASE_KINDS:
        if not write_path.is_file():
            findings.append(finding(
                rule="RULE-PKG-030",
                message_id="write-map-missing", kind="fail", path="/",
                message=f"{kind} connector requires sibling {_WRITE_MAP_FILENAME}; missing."))
            return findings
        write_doc, load = _load_type_map(write_path)
        findings.extend(load)
        if write_doc is not None:
            findings.extend(type_map_findings(write_doc, "write"))
        return findings

    # api: no write map, and every endpoint's natives must be covered by the read map.
    if write_path.is_file():
        findings.append(finding(
            rule="RULE-PKG-030",
            message_id="write-map-not-allowed", kind="fail", path="/",
            message=(
                f"api connector must not ship {_WRITE_MAP_FILENAME}; the write direction "
                "is database-only.")))
    if not isinstance(read_rules, list):
        # notApplicable, not fail: the check knows exactly which rule it would
        # be grading (RULE-PKG-033) — the read map itself is missing, unreadable,
        # or malformed, which is already reported above under its own rule; this
        # says the coverage question specifically was never answered.
        findings.append(finding(
            rule="RULE-PKG-033",
            message_id="native-type-coverage-skipped", kind="notApplicable", path="/",
            message=(
                f"native_type coverage against sibling {_READ_MAP_FILENAME} was not "
                "rendered: the map is missing, unreadable, or its `rules` is not a "
                "list. Endpoint native_type/arrow_type agreement is unverified until "
                "it is fixed.")))
    endpoint_dir = parent / "endpoints"
    if not endpoint_dir.is_dir():
        findings.append(finding(
            rule="RULE-PKG-035",
            message_id="endpoints-dir-missing", kind="fail", path="/",
            message="api connector requires a sibling 'endpoints/' directory; missing."))
        return findings
    # Scan recursively, matching the registry merge gate: every *.json under
    # endpoints/ must sit at exactly `endpoints/{endpoint_id}.json` (flat) — a
    # nested/misplaced file is rejected there, so the validator flags it too
    # rather than reporting a false pass.
    endpoint_files = sorted(endpoint_dir.rglob("*.json"))
    if not endpoint_files:
        findings.append(finding(
            rule="RULE-PKG-035",
            message_id="endpoints-dir-empty", kind="fail", path="/",
            message="api connector's 'endpoints/' directory has no *.json files."))
        return findings
    # Cross-endpoint identity: `endpoint_id` is unique within the connector
    # release (the contract's shared-metadata rules). The
    # filename==id rule only makes IDENTICAL ids collide on the filesystem (and
    # then surfaces obliquely as a filename mismatch); enforce the invariant
    # directly so a duplicate is reported as a duplicate.
    seen_ids: dict[str, str] = {}
    for ep_path in endpoint_files:
        rel = ep_path.relative_to(endpoint_dir).as_posix()
        if "/" in rel:
            findings.append(finding(
                rule="RULE-PKG-031",
                message_id="endpoint-file-nested", kind="fail", path="/",
                message=(
                    f"endpoint file 'endpoints/{rel}' is nested; endpoints must be flat "
                    "at 'endpoints/{endpoint_id}.json' (the engine resolves them by id).")))
            continue
        ep_doc, load = _load_json_sibling(
            ep_path, rule=None, message_id="endpoint-file-unreadable")
        if ep_doc is None:
            findings.extend(load)
            continue
        # Each sibling endpoint is a full api-endpoint document — validate it
        # with the checks shared with the standalone single-document route.
        findings.extend(_api_endpoint_document_findings(
            ep_doc, doc.get("transports"), filename=ep_path.name))
        ep_id = ep_doc.get("endpoint_id") if isinstance(ep_doc, dict) else None
        if isinstance(ep_id, str) and ep_id:
            if ep_id in seen_ids:
                findings.append(finding(
                    rule="RULE-PKG-032",
                    message_id="duplicate-endpoint-id", kind="fail", path="/endpoint_id",
                    message=(
                        f"duplicate endpoint_id {ep_id!r}: declared by both "
                        f"'endpoints/{seen_ids[ep_id]}' and 'endpoints/{ep_path.name}'; "
                        "endpoint_id must be unique within the connector release.")))
            else:
                seen_ids[ep_id] = ep_path.name
        if not isinstance(ep_doc, dict):
            # A JSON array/string endpoint file is already a recorded model error;
            # skip the coverage walk (it calls `.get()` and would crash, replacing
            # the actionable findings with a generic "validator bug" via _run_guarded).
            continue
        # The rendering is the one check here that needs the sibling map; a map
        # that did not load skips it, and the connector-level warning says so.
        if isinstance(read_rules, list):
            for native, arrow, pointer in _collect_native_arrow_pairs(ep_doc):
                rendered = _render_arrow_type(native, read_rules)
                site = f"{ep_path.name}{pointer}"
                if rendered is None:
                    findings.append(finding(
                        rule="RULE-PKG-033",
                        message_id="native-type-unresolved", kind="fail", path="/",
                        message=(
                            f"native_type {native!r} at {site} has no matching rule in "
                            f"sibling {_READ_MAP_FILENAME}.")))
                elif not _arrow_type_eq(rendered, arrow) and not (rendered == "Json" and arrow in _NARROWING_ARROW_TYPES):
                    findings.append(finding(
                        rule="RULE-PKG-033",
                        message_id="native-type-arrow-mismatch", kind="fail", path="/",
                        message=(
                            f"native_type {native!r} at {site} resolves to {rendered!r} via "
                            f"{_READ_MAP_FILENAME} but the endpoint declares arrow_type={arrow!r}.")))
    return findings


# ---------------------------------------------------------------------------
# Adapters (built once)
# ---------------------------------------------------------------------------

_CONNECTOR_ADAPTER = TypeAdapter(Connector)
_API_ENDPOINT_ADAPTER = TypeAdapter(ApiEndpointDoc)
_DATABASE_ENDPOINT_ADAPTER = TypeAdapter(DatabaseEndpointDoc)
_READ_MAP_ADAPTER = TypeAdapter(TypeMapReadDoc)
_WRITE_MAP_ADAPTER = TypeAdapter(TypeMapWriteDoc)


# ---------------------------------------------------------------------------
# Per-kind validators + registration
# ---------------------------------------------------------------------------

def _validate_connector(doc: Any, doc_path: Path | None) -> list[dict]:
    findings = _model_findings(doc, _CONNECTOR_ADAPTER)
    findings += _missing_schema_url_findings(doc)
    findings += check_coverage(doc, doc_path)
    return findings


def _validate_api_endpoint(doc: Any, doc_path: Path | None) -> list[dict]:
    transports: Any = None
    sibling_findings: list[dict] = []
    if isinstance(doc, dict):
        # RULE-ENDP-047 is cross-document: it needs the sibling connector.json's
        # `transports`, which only `check_coverage` has. Say so rather than
        # returning a silent clean pass — an author validating a single
        # endpoint file would otherwise read `passed: true` as "the
        # transport_ref is fine", which is reassurance the check never earned.
        # notApplicable, not fail, matching `endpoint_filename_findings`'s
        # convention for a check it cannot perform from the given path.
        declared_refs = sorted({
            ref for _, ref in _api_operation_transport_refs(doc) if isinstance(ref, str)
        })
        if declared_refs:
            # Resolve the sibling connector when the layout gives us one:
            # `endpoints/{id}.json` sits one level below `connector.json`. The
            # connector-builder skill validates each endpoint on its own, so a
            # blind warning here would fire on every pass of its fix loop and
            # could never be cleared — an alarm that cannot be acted on trains
            # authors to ignore the id. Only warn when the connector genuinely
            # is not reachable.
            # `.resolve()` first: `Path("things.json").parent.parent` is `.`, so
            # validating with a relative `--document` from inside `endpoints/`
            # missed the sibling and downgraded a genuinely broken ref to a
            # warning — a silent pass on the one check this adds. `..` in the
            # path failed the same way.
            sibling = (
                doc_path.resolve().parent.parent / "connector.json"
                if doc_path
                else None
            )
            connector_doc = None
            sibling_exists = sibling is not None and sibling.is_file()
            if sibling_exists:
                # `rule=None`: a read/parse failure has not evaluated
                # RULE-ENDP-047 one way or the other, so asserting a `fail`
                # against it here would contradict the `notApplicable` the
                # `elif sibling_exists` branch below reports for the identical
                # case. The failure to read is a framework-level fact; which
                # rule went unchecked as a result is that branch's to name.
                connector_doc, load_findings = _load_json_sibling(
                    sibling, rule=None, message_id="sibling-connector-unreadable",
                )
                sibling_findings.extend(load_findings)
            transports = connector_doc.get("transports") if isinstance(connector_doc, dict) else None
            # `transports` resolved: RULE-ENDP-047 is graded against it by
            # `_api_endpoint_document_findings` below. What is left to report
            # here is the cases where it could not be resolved, each naming
            # which one happened.
            if not isinstance(transports, dict):
                if connector_doc is not None:
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
                        kind="notApplicable", path="/",
                        message=(
                            f"transport_ref {declared_refs!r} not checked: the sibling "
                            "connector.json was read but declares no usable `transports` "
                            "object, so there was nothing to resolve the name against. "
                            "Validate the connector to see why.")))
                elif sibling_exists:
                    # The file IS there and WAS read — it just did not parse.
                    # Branching on `connector_doc is None` alone said "not
                    # reachable", contradicting the parse error emitted beside it
                    # under the same id.
                    sibling_findings.append(finding(
                        rule="RULE-ENDP-047",
                        message_id="transport-ref-check-skipped-unparseable",
                        kind="notApplicable", path="/",
                        message=(
                            f"transport_ref {declared_refs!r} not checked: the sibling "
                            f"connector.json at {sibling} could not be parsed, so its "
                            "`transports` could not be read. Fix the error reported "
                            "above and re-run.")))
                else:
                    sibling_findings.append(finding(
                        rule="RULE-ENDP-047",
                        message_id="transport-ref-check-skipped-no-sibling",
                        kind="notApplicable", path="/",
                        message=(
                            f"transport_ref {declared_refs!r} not checked: no sibling "
                            "connector.json was reachable from this document's path, so "
                            "its `transports` could not be read. Validate the connector "
                            "to resolve it.")))
    # Each api-endpoint document goes through the checks shared with
    # `check_coverage`'s sibling-endpoint loop; `transports` is None wherever
    # the branches above could not resolve it, and RULE-ENDP-047 stays silent
    # there rather than reporting on an unresolved comparison. Silence is the
    # whole answer only when the document declares no `transport_ref` at all —
    # every other way of arriving here with `transports` unresolved has already
    # appended the `notApplicable` naming which one it was. The filename rides
    # along so the locating half of those checks reads the same on both routes.
    findings = _api_endpoint_document_findings(
        doc, transports,
        filename=(doc_path.name
                  if doc_path is not None and is_addressed_endpoint_path(doc_path)
                  else ""))
    findings.extend(sibling_findings)
    return findings


def _validate_database_endpoint(doc: Any, doc_path: Path | None) -> list[dict]:
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
    if doc_path is not None and is_stem_addressed_endpoint_path(doc_path):
        findings += endpoint_filename_findings(doc, doc_path.name)
    return findings


def _validate_type_map(doc: Any, doc_path: Path | None) -> list[dict]:  # skipcq: PYL-W0613 — uniform registered-validator signature
    # The engine keys a type map by the document's own `direction`, so the
    # filename never grades it. `$schema` names the direction too and stands in
    # when `direction` is missing or invalid, so the rest of the document is
    # graded against the model its author meant; with neither, the read model
    # reports the missing `direction` itself.
    direction = doc.get("direction")
    if direction not in ("read", "write"):
        direction = "write" if doc.get("$schema") == TYPE_MAP_WRITE_SCHEMA_URL else "read"
    return type_map_findings(doc, direction)


def _validate_kindless_connector(doc: Any, doc_path: Path | None) -> list[dict]:  # skipcq: PYL-W0613 — uniform registered-validator signature
    # A dict carrying connector sentinels but no `kind` is a connector missing
    # its discriminator — hand it to the model so the missing `kind` is reported
    # (rather than silently passing as "unrecognized"). `$schema` is optional on
    # the connector model, so RULE-SHRD-003 is the only thing reporting its
    # omission; the kind-bearing route runs it, and which route a document
    # reaches must not decide what it is told.
    return _model_findings(doc, _CONNECTOR_ADAPTER) + _missing_schema_url_findings(doc)


# Registration order mirrors the original dispatch precedence: connector,
# api-endpoint, database-endpoint, type-map (an object carrying a `rules` key
# — no other document kind has one at the top level), then the
# kindless-connector fallback. Sniffing on `rules` alone (not also requiring a
# valid `direction`) means a doc with a missing/malformed `direction` still
# reaches the model and gets that specific error, rather than falling through
# to the generic "unrecognized document" verdict. `_core._dispatch` runs these
# in order and falls through to that verdict if none match.
register_kind(is_connector_doc, _validate_connector)
register_kind(is_api_endpoint_doc, _validate_api_endpoint)
register_kind(is_database_endpoint_doc, _validate_database_endpoint)
register_kind(lambda doc: isinstance(doc, dict) and "rules" in doc, _validate_type_map)
register_kind(
    lambda doc: isinstance(doc, dict) and any(k in doc for k in _CONNECTOR_SENTINELS),
    _validate_kindless_connector,
)
