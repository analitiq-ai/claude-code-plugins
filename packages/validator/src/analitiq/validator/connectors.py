"""Connector-package validation — the connector / api-endpoint / database-endpoint
/ type-map artifact kinds.

Single-document validity is delegated to the Pydantic **contract models**
(`analitiq-contract-models`, the same models the published JSON Schemas are
generated from): each document kind is validated with `TypeAdapter(...).
validate_python`, which enforces structure *and* every cross-field rule the
contract defines — offline, no schema fetch, no drift. On top of the models this
module adds only what a single-document model cannot express:

- **cross-file coverage** (`type-map-coverage`): a connector must ship the right
  sibling type-map files for its kind, and an API connector's read map must
  cover every `(native_type, arrow_type)` its endpoint files declare;
- **filename ↔ id** (`endpoint-filename`): an endpoint file must be named
  `{endpoint_id}.json`;
- **endpoint id uniqueness** (`endpoint-id-unique`): each `endpoint_id` is unique
  within the connector release;
- **endpoint id ↔ locator** (`endpoint-id-locator`): an `endpoint_id` equals the
  handle derived from its locator — an API id from its `operations.*.request.path`
  (lowercase, `__` between path levels, path-params dropped) so `/v1/x` and `/v2/x`
  cannot collide (the same rule the contract uses to derive `resources[].key`);
  a database id from its verbatim
  `database_object` (`slug(schema)__slug(table)[__slug(catalog)]__hash8`, via the
  shared `analitiq.contracts.endpoint_identity`);
- **endpoint → transport** (`endpoint-transport-ref`): an endpoint's
  `request.transport_ref` must name a transport the sibling connector.json
  declares. `ConnectorBase._transport_refs_resolvable` enforces the same rule for
  every connector-internal ref site, but an endpoint document is a separate file
  and structurally invisible to that model validator — so the cross-file half of
  the rule lives here;
- **advisory quality warnings** the contract tolerates: duplicate type-map
  rules, dead uppercase-only read patterns, and write-map vocabulary gaps.

At import this module registers its detector→validator pairs and its validator
ids with the core dispatch registry, so `_core` never hard-codes connector
branches.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

from ._core import (
    contract_model_domain,
    finding,
    register_kind,
    register_validator_ids,
    _model_findings,
    _run_guarded,
)

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
            SAMPLE_CONTRADICTION_REMEDY,
        )
        # RULE-ENDP-026's own walk, imported rather than approximated: it is
        # what decides whether every `$ref` in an embedded schema resolves and
        # terminates, and a sample cannot be graded through one that does not.
        # The pointer escape travels with it — the contract owns both halves of
        # RFC 6901 (`_unescape_pointer_token` is what reads a `$ref`), and a
        # second implementation here would be a copy of a value with an owner.
        from analitiq.contracts.endpoints import (
            _validate_schema_refs,
            iter_schema_nodes,
        )
        from analitiq.contracts.endpoint_identity import derive_db_endpoint_id
        from analitiq.contracts.type_map import TypeMapReadDoc, TypeMapWriteDoc
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
    print(json.dumps({
        "passed": False,
        "findings": [{
            "validator": "contract-model",
            "severity": "error",
            "path": "",
            "message": f"Missing dependency: {exc}. Install `analitiq-contract-models`.",
        }],
    }))
    sys.exit(1)

register_validator_ids({
    "type-map-coverage",
    "type-map-rule",
    "type-map-write-coverage",
    "endpoint-filename",
    "endpoint-id-unique",
    "endpoint-id-locator",
    "endpoint-transport-ref",
    "embedded-json-schema",
    "embedded-schema-example",
})


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

    For read maps `normalize` is the canonical `normalize_native_type` (imported
    as `_normalize_native`) and is applied to BOTH sides of an `exact`
    comparison — the incoming probe and the rule's `native` matcher — because
    every runtime reader normalizes an exact rule's `native` the same way it
    normalizes the lookup value, so the two must agree here too. A `regex`
    matcher is never normalized (uppercasing would turn `\\d` into `\\D`); only
    its probe is. `normalize` is None for write maps, where the `canonical`
    matcher is compared as authored.
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


def _render_canonical(native: str, rules: list) -> str | None:
    return _first_match_render(native, rules, "native", "canonical", normalize=_normalize_native)


# Collapse whitespace ONLY around Arrow separators — not inside identifiers.
_CANONICAL_SEP_WS = re.compile(r"\s*([,()])\s*")


def _canonical_eq(a: str, b: str) -> bool:
    """Compare two Arrow canonical types ignoring separator spacing only. The
    intra-parameter spacing of `Decimal128(38, 9)` vs `Decimal128(38,9)` is not
    significant, but whitespace INSIDE a token IS (`Time stamp(SECOND)` is not
    `Timestamp(SECOND)`) — so whitespace is collapsed only around the Arrow
    separators (`,()`, the vocabulary's only SEPARATOR punctuation; `:`/`/`/`+`
    occur only inside timezone tokens, where whitespace stays significant),
    never deleted wholesale."""
    norm = lambda s: _CANONICAL_SEP_WS.sub(r"\1", s).strip()  # noqa: E731
    return norm(a) == norm(b)


def _walk_schema_pairs(schema: Any, pointer: str, out: list[tuple[str, str, str]]) -> None:
    """Collect `(native_type, arrow_type)` pairs from a JSON Schema."""
    for node_pointer, node in iter_schema_nodes(schema, pointer):
        nt, at = node.get("native_type"), node.get("arrow_type")
        if isinstance(nt, str) and isinstance(at, str):
            out.append((nt, at, node_pointer))


def _collect_native_arrow_pairs(ep_doc: dict) -> list[tuple[str, str, str]]:
    """Every `(native_type, arrow_type)` pair on the endpoint's typed field
    schemas — `operations.read.response.schema` and each
    `operations.write.<mode>.input.schema`. Walks the schemas structurally so a
    field named `default`/`const` is covered but a literal-data value is not."""
    out: list[tuple[str, str, str]] = []
    ops = ep_doc.get("operations")
    if not isinstance(ops, dict):
        return out
    read = ops.get("read")
    if isinstance(read, dict) and isinstance(read.get("response"), dict):
        _walk_schema_pairs(read["response"].get("schema"),
                           "/operations/read/response/schema", out)
    write = ops.get("write")
    if isinstance(write, dict):
        for mode, block in write.items():
            if isinstance(block, dict) and isinstance(block.get("input"), dict):
                _walk_schema_pairs(block["input"].get("schema"),
                                   f"/operations/write/{mode}/input/schema", out)
    return out


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
                out.append((f"/operations/write/{mode}/input/schema", block["input"].get("schema")))
    return out


_DRAFT_2020_12_SCHEMA = "https://json-schema.org/draft/2020-12/schema"


def _embedded_schema_findings(ep_doc: dict, label: str = "") -> list[dict]:
    """Each embedded input/response schema must be a valid JSON Schema
    Draft 2020-12 document. The contract model checks the arrow_type/native_type
    pairing but not meta-schema validity, so this is the validator's job. A
    non-dict schema is already a recorded model error and is skipped here.

    `check_schema` validates keyword-validity against the 2020-12 meta-schema but
    does NOT verify the document's own `$schema` dialect, so a schema DECLARING
    another draft (e.g. Draft-07) could otherwise slip through — reject a
    non-2020-12 `$schema` explicitly. An absent `$schema` is allowed (the engine
    reads these as 2020-12; a valid authored write `input.schema` may omit it).

    `jsonschema` is imported lazily HERE, not at module load: some callers import
    `analitiq.validator` only to run `validate_pipeline_bundle` and never reach
    this api-endpoint path, so a module-level import would force `jsonschema` onto
    every consumer even where it is not installed. Only endpoint meta-validation
    needs it."""
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    findings: list[dict] = []
    for pointer, schema in _embedded_json_schemas(ep_doc):
        if not isinstance(schema, dict):
            continue
        where = f"{label}{pointer}" if label else pointer
        declared = schema.get("$schema")
        if declared is not None and declared != _DRAFT_2020_12_SCHEMA:
            findings.append(finding(
                "embedded-json-schema", "error", pointer,
                f"embedded schema at {where} declares $schema {declared!r}; the "
                f"contract requires JSON Schema Draft 2020-12 "
                f"({_DRAFT_2020_12_SCHEMA!r}) or no $schema"))
            continue
        nested = sorted(
            node_pointer
            for node_pointer, node in iter_schema_nodes(schema)
            if node_pointer and "$schema" in node
        )
        if nested:
            findings.append(finding(
                "embedded-json-schema", "error", pointer,
                f"embedded schema at {where} declares `$schema` at "
                f"{', '.join(nested)}. The keyword sets the dialect for a "
                "resource, and only a resource ROOT is one — `$id` is not "
                "authorable here, so no subschema is a root and a `$schema` "
                "below the top level names a dialect nothing agrees on: a JSON "
                "Schema implementation switches to it for that subtree, while "
                "this contract and the engine read the whole document in "
                f"{_DRAFT_2020_12_SCHEMA!r}. Declare it once at the top, or "
                "not at all"))
            continue
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            findings.append(finding(
                "embedded-json-schema", "error", pointer,
                f"embedded schema at {where} is not a valid JSON Schema Draft "
                f"2020-12 document: {exc.message}"))
            continue
        # Guarded, because grading an instance is the one thing here that
        # runs arbitrary keyword logic: `multipleOf` against a 400-digit
        # sample raises `OverflowError` out of `iter_errors`, and the
        # metaschema has no opinion about it. Unguarded, the dispatch-level
        # guard turns that into one "validator bug" line REPLACING every
        # finding on the document — including the ones the author was fixing.
        # Guarded here, a crash costs this check and nothing else.
        findings.extend(_run_guarded(
            _schema_example_findings, schema, pointer, where,
            vid="embedded-schema-example"))
    return findings


def _schema_example_findings(schema: dict, pointer: str, where: str) -> list[dict]:
    """RULE-ENDP-064: every `examples` entry must satisfy the node declaring it.

    An `examples` entry on a typed node is the wire sample the field's
    declaration was decided from, so a sample the node itself rejects is a
    declaration contradicted by its own evidence — the shape that ships a
    connector which validates clean and dies on the first batch.

    Each node is graded by a validator `evolve`d from one built on the WHOLE
    embedded document, never by one built on the node alone: a `#/$defs/...`
    reference inside a node resolves against the document root, so a validator
    that had only the node would call every legitimate reference unresolvable.

    Reached only for a schema `check_schema` already accepted — grading an
    instance against a malformed schema reports the malformation a second time,
    in the vocabulary of whichever example happened to meet it first. A schema
    whose references the contract refuses is skipped for the same reason and
    one more: resolution happens here for the first time in this module, so a
    reference RULE-ENDP-026 objects to either raises out of `iter_errors` or
    resolves to something other than what the contract read. That rule names
    each of them precisely; this check has nothing to add to it.

    Neither gate makes `iter_errors` total, and this does not try to be — the
    caller runs it guarded, so a keyword that raises costs this check's
    findings rather than the document's.

    `jsonschema` is imported HERE for the reason the caller states: only
    endpoint meta-validation needs it.
    """
    from jsonschema import Draft202012Validator

    nodes = [
        (node_pointer, node)
        for node_pointer, node in iter_schema_nodes(schema)
        if isinstance(node.get("examples"), list)
    ]
    if not nodes:
        return []
    refused_references: list[str] = []
    _validate_schema_refs(schema, pointer, refused_references)
    if refused_references:
        return []
    root = Draft202012Validator(schema)
    findings: list[dict] = []
    for node_pointer, node in nodes:
        validator = root.evolve(schema=node)
        for index, example in enumerate(node["examples"]):
            error = next(validator.iter_errors(example), None)
            if error is None:
                continue
            at = f"{where}{node_pointer}" if node_pointer else where
            findings.append(finding(
                "embedded-schema-example", "error",
                f"{pointer}{node_pointer}/examples/{index}",
                f"recorded sample {example!r} at {at} does not satisfy the "
                f"declaration it sits on: {error.message}. "
                f"{SAMPLE_CONTRADICTION_REMEDY}"))
    return findings


# Representative canonicals a write map should render; gaps are warnings (a
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
    """One representative canonical for a family: each REQUIRED parameter takes
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
    """Warn when a write map renders no rule for a canonical family."""
    missing = [
        probe for probe in _WRITE_VOCABULARY_PROBES
        if _first_match_render(probe, rules, "canonical", "native") is None
    ]
    if not missing:
        return []
    return [finding(
        "type-map-write-coverage", "warning", "/",
        f"write map has no rule rendering these canonical families: {missing}. "
        "If the dialect renders them via a column-type override this is expected; "
        "otherwise add rules so they materialize.",
    )]


def _type_map_rule_warnings(rules: list, direction: str) -> list[dict]:
    """Advisory (non-error) type-map checks the contract tolerates: duplicate
    rules (later ones unreachable) and read patterns that can never match."""
    if not isinstance(rules, list):
        return []
    matcher_key = "native" if direction == "read" else "canonical"
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
                    "type-map-rule", "warning", f"/{i}",
                    f"duplicate rule for (match={match!r}, {matcher_key}={matcher!r}); "
                    "first-match-wins makes later duplicates unreachable.",
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
                    "type-map-rule", "warning", f"/{i}/{matcher_key}",
                    f"regex {matcher_key} is matched against UPPERCASED natives; "
                    f"lowercase literals in {matcher!r} can never match.",
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
    """Derive an API `endpoint_id` handle from a request path, per the authoring
    contract's `resources[].key` derivation rule: lowercase,
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
            "endpoint-transport-ref", "error", pointer,
            f"{where} transport_ref={ref!r} is not declared in the sibling "
            f"connector.json `transports` (declared: {sorted(transports)!r}; "
            "spec: §Transport Selection). A request dispatches only through a "
            "transport the connector declares."))
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
            "endpoint-id-locator", "error", pointer,
            f"cannot derive a stable endpoint_id from request.path {path!r} — it "
            f"flattens to {handle!r}, which is empty or carries characters outside "
            f"the id charset ({SLUG_RE.pattern}). The derivation assumes charset-"
            "safe, non-empty paths; rename the path (e.g. drop a '.json' suffix) or "
            "extend the derivation rule with sanitization.")]
    if handle != endpoint_id:
        return [finding(
            "endpoint-id-locator", "error", "/endpoint_id",
            f"endpoint_id {endpoint_id!r} must equal {handle!r} — the handle derived from "
            f"request.path {path!r} (lowercase, '__' between path levels, path-params "
            "dropped) — so distinct paths like /v1/x and /v2/x get distinct ids.")]
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
            "endpoint-id-locator", "error", "/endpoint_id",
            f"endpoint_id {endpoint_id!r} must equal {expected!r} — the handle derived "
            "from database_object (slug(schema)__slug(table)[__slug(catalog)]__hash8, "
            "per analitiq.contracts.endpoint_identity). The verbatim identity stays in "
            "database_object; the id is a derived handle, never parsed back.")]
    return []


def endpoint_filename_findings(ep_doc: Any, filename: str) -> list[dict]:
    """Public gate: an endpoint file must be named `{endpoint_id}.json`.

    Returns standard `{validator, severity, path, message}` findings carrying the
    `endpoint-filename` id — an error when `filename` disagrees with the doc's
    `endpoint_id`, a warning when the id is missing/unusable, empty when they
    agree. Exported so a filesystem-walking consumer that assembles a pipeline
    bundle (and so cannot reach the gate through `validate_document`, whose bundle
    entry point takes filename-less in-memory docs) enforces the invariant through
    this one shared implementation instead of duplicating it. Pair with
    `is_stem_addressed_endpoint_path` to apply the gate on the same layout
    condition the validator uses."""
    if not isinstance(ep_doc, dict):
        return []
    endpoint_id = ep_doc.get("endpoint_id")
    if not isinstance(endpoint_id, str) or not endpoint_id:
        return [finding(
            "endpoint-filename", "warning", "/endpoint_id",
            f"endpoint file {filename!r} has no usable string endpoint_id; "
            "cannot verify the filename matches.",
        )]
    expected = f"{endpoint_id}.json"
    if filename != expected:
        return [finding(
            "endpoint-filename", "error", "/endpoint_id",
            f"endpoint file is named {filename!r} but endpoint_id is {endpoint_id!r}; "
            f"it must be named {expected!r} (the engine locates endpoints/{{endpoint_id}}.json).",
        )]
    return []


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


def _load_json_sibling(path: Path, validator_id: str) -> tuple[Any, list[dict]]:
    """Read a sibling JSON document, reporting a read/parse failure under
    ``validator_id``.

    The id is a parameter because the callers are different checks. It used to
    be hardcoded to `type-map-coverage` and the endpoint-anchored caller
    relabelled the findings afterwards — so a connector.json that would not
    parse was reported against an endpoint under the type-map id, invisible to a
    fix loop filtering on the check that actually failed. Naming the reporter at
    the call site fixes that at the source instead of rewriting its output.
    """
    try:
        return json.loads(path.read_text()), []
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, [finding(validator_id, "error", "/",
                              f"sibling {path.name} could not be read or parsed ({exc}).")]


def _load_type_map(path: Path) -> tuple[list | None, list[dict]]:
    """A type-map document, or `None` plus a `type-map-coverage` finding."""
    return _load_json_sibling(path, "type-map-coverage")


def _type_map_findings(doc: Any, direction: str) -> list[dict]:
    """Validate a loaded type-map document: model errors + advisory rule
    warnings + (write-vocabulary coverage on the write direction). The single
    definition used everywhere a type-map is checked — standalone, or as a
    connector's sibling."""
    adapter = _READ_MAP_ADAPTER if direction == "read" else _WRITE_MAP_ADAPTER
    findings = _model_findings(doc, adapter)
    findings.extend(_type_map_rule_warnings(doc, direction))
    if direction == "write" and isinstance(doc, list):
        findings.extend(_write_vocabulary_findings(doc))
    return findings


def check_coverage(doc: dict, doc_path: Path | None) -> list[dict]:
    """Connector ↔ sibling type-map coverage (the irreducibly cross-file check)."""
    if not isinstance(doc, dict) or not any(k in doc for k in _CONNECTOR_SENTINELS):
        return []
    if doc_path is None:
        return [finding("type-map-coverage", "warning", "/",
                        "type-map coverage skipped: no filesystem-anchored document path.")]
    kind = doc.get("kind")
    if kind not in ("api", *_DATABASE_KINDS, *_STORAGE_KINDS):
        return [finding("type-map-coverage", "warning", "/kind",
                        f"type-map coverage skipped: connector 'kind'={kind!r} is not in the "
                        "closed enum (the model enforces this).")]

    findings: list[dict] = []
    parent = doc_path.parent
    read_path, write_path = parent / _READ_MAP_FILENAME, parent / _WRITE_MAP_FILENAME
    if (parent / _LEGACY_MAP_FILENAME).is_file():
        findings.append(finding("type-map-coverage", "error", "/",
                                f"sibling {_LEGACY_MAP_FILENAME} is the pre-split name; rename the "
                                f"read direction to {_READ_MAP_FILENAME} (and add {_WRITE_MAP_FILENAME} "
                                "for database connectors)."))

    if kind in _STORAGE_KINDS:
        for path, direction in ((read_path, "read"), (write_path, "write")):
            if path.is_file():
                doc_, load = _load_type_map(path)
                findings.extend(load)
                if doc_ is not None:
                    findings.extend(_type_map_findings(doc_, direction))
        return findings

    if not read_path.is_file():
        findings.append(finding("type-map-coverage", "error", "/",
                                f"connector requires sibling {_READ_MAP_FILENAME} (native → Arrow); missing."))
        return findings
    read_doc, load = _load_type_map(read_path)
    findings.extend(load)
    if read_doc is None:
        return findings
    findings.extend(_type_map_findings(read_doc, "read"))

    if kind in _DATABASE_KINDS:
        if not write_path.is_file():
            findings.append(finding("type-map-coverage", "error", "/",
                                    f"{kind} connector requires sibling {_WRITE_MAP_FILENAME}; missing."))
            return findings
        write_doc, load = _load_type_map(write_path)
        findings.extend(load)
        if write_doc is not None:
            findings.extend(_type_map_findings(write_doc, "write"))
        return findings

    # api: no write map, and every endpoint's natives must be covered by the read map.
    if write_path.is_file():
        findings.append(finding("type-map-coverage", "error", "/",
                                f"api connector must not ship {_WRITE_MAP_FILENAME}; the write direction "
                                "is database-only."))
    endpoint_dir = parent / "endpoints"
    if not endpoint_dir.is_dir():
        findings.append(finding("type-map-coverage", "error", "/",
                                "api connector requires a sibling 'endpoints/' directory; missing."))
        return findings
    # Scan recursively, matching the registry merge gate: every *.json under
    # endpoints/ must sit at exactly `endpoints/{endpoint_id}.json` (flat) — a
    # nested/misplaced file is rejected there, so the validator flags it too
    # rather than reporting a false pass.
    endpoint_files = sorted(endpoint_dir.rglob("*.json"))
    if not endpoint_files:
        findings.append(finding("type-map-coverage", "error", "/",
                                "api connector's 'endpoints/' directory has no *.json files."))
        return findings
    if not isinstance(read_doc, list):
        return findings  # model error already recorded; can't render coverage

    # Cross-endpoint identity: `endpoint_id` is unique within the connector
    # release (the contract's shared-metadata rules). The
    # filename==id rule only makes IDENTICAL ids collide on the filesystem (and
    # then surfaces obliquely as a filename mismatch); enforce the invariant
    # directly so a duplicate is reported as a duplicate.
    seen_ids: dict[str, str] = {}
    for ep_path in endpoint_files:
        rel = ep_path.relative_to(endpoint_dir).as_posix()
        if "/" in rel:
            findings.append(finding("type-map-coverage", "error", "/",
                                    f"endpoint file 'endpoints/{rel}' is nested; endpoints must be flat "
                                    "at 'endpoints/{endpoint_id}.json' (the engine resolves them by id)."))
            continue
        ep_doc, load = _load_json_sibling(ep_path, "type-map-coverage")
        if ep_doc is None:
            findings.extend(load)
            continue
        # Each sibling endpoint is a full api-endpoint document — validate it
        # with the model (annotations, markers, wiring) and check its filename.
        findings.extend(_model_findings(ep_doc, _API_ENDPOINT_ADAPTER))
        findings.extend(endpoint_filename_findings(ep_doc, ep_path.name))
        findings.extend(_endpoint_locator_findings(ep_doc))
        ep_id = ep_doc.get("endpoint_id") if isinstance(ep_doc, dict) else None
        if isinstance(ep_id, str) and ep_id:
            if ep_id in seen_ids:
                findings.append(finding(
                    "endpoint-id-unique", "error", "/endpoint_id",
                    f"duplicate endpoint_id {ep_id!r}: declared by both "
                    f"'endpoints/{seen_ids[ep_id]}' and 'endpoints/{ep_path.name}'; "
                    "endpoint_id must be unique within the connector release."))
            else:
                seen_ids[ep_id] = ep_path.name
        if not isinstance(ep_doc, dict):
            # A JSON array/string endpoint file is already a recorded model error;
            # skip the coverage walk (it calls `.get()` and would crash, replacing
            # the actionable findings with a generic "validator bug" via _run_guarded).
            continue
        findings.extend(_embedded_schema_findings(ep_doc, label=ep_path.name))
        # Cross-file: the endpoint's transport_ref sites resolve against THIS
        # connector's `transports` — checkable only here, where both documents
        # are in hand.
        findings.extend(_endpoint_transport_ref_findings(
            ep_doc, doc.get("transports"), label=ep_path.name))
        for native, arrow, pointer in _collect_native_arrow_pairs(ep_doc):
            rendered = _render_canonical(native, read_doc)
            site = f"{ep_path.name}{pointer}"
            if rendered is None:
                findings.append(finding("type-map-coverage", "error", "/",
                                        f"native_type {native!r} at {site} has no matching rule in "
                                        f"sibling {_READ_MAP_FILENAME}."))
            elif not _canonical_eq(rendered, arrow) and not (rendered == "Json" and arrow in _NARROWING_ARROW_TYPES):
                findings.append(finding("type-map-coverage", "error", "/",
                                        f"native_type {native!r} at {site} resolves to {rendered!r} via "
                                        f"{_READ_MAP_FILENAME} but the endpoint declares arrow_type={arrow!r}."))
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

def _validate_connector(doc: Any, doc_path: Path | None, schema_url: str | None = None) -> list[dict]:  # skipcq: PYL-W0613 — uniform registered-validator signature
    findings = _model_findings(doc, _CONNECTOR_ADAPTER)
    findings += check_coverage(doc, doc_path)
    return findings


def _validate_api_endpoint(doc: Any, doc_path: Path | None, schema_url: str | None = None) -> list[dict]:  # skipcq: PYL-W0613 — uniform registered-validator signature
    findings = _model_findings(doc, _API_ENDPOINT_ADAPTER)
    findings += _endpoint_locator_findings(doc)
    if isinstance(doc, dict):
        findings += _embedded_schema_findings(doc)
        # `endpoint-transport-ref` is cross-document: it needs the sibling
        # connector.json's `transports`, which only `check_coverage` has. Say so
        # rather than returning a silent clean pass — an author validating a
        # single endpoint file would otherwise read `passed: true` as "the
        # transport_ref is fine", which is reassurance the check never earned.
        # Warning, not error, matching `endpoint-filename`'s
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
                connector_doc, load_findings = _load_json_sibling(
                    sibling, "endpoint-transport-ref"
                )
                findings.extend(load_findings)
            transports = connector_doc.get("transports") if isinstance(connector_doc, dict) else None
            if isinstance(transports, dict):
                findings.extend(_endpoint_transport_ref_findings(
                    doc, transports, label=doc_path.name if doc_path else ""))
            elif connector_doc is not None:
                # Connector found, but its `transports` is missing or not an
                # object. `_endpoint_transport_ref_findings` returns [] there —
                # correct at the CONNECTOR-anchored call site, where the
                # connector's own model error already stands. Here the connector
                # model never runs, so returning [] would report a clean pass on
                # an endpoint whose `transport_ref` resolves to nothing. Say what
                # could not be checked and why.
                findings.append(finding(
                    "endpoint-transport-ref", "warning", "/",
                    f"transport_ref {declared_refs!r} not checked: the sibling "
                    "connector.json was read but declares no usable `transports` "
                    "object, so there was nothing to resolve the name against. "
                    "Validate the connector to see why."))
            elif sibling_exists:
                # The file IS there and WAS read — it just did not parse.
                # Branching on `connector_doc is None` alone said "not
                # reachable", contradicting the parse error emitted beside it
                # under the same id.
                findings.append(finding(
                    "endpoint-transport-ref", "warning", "/",
                    f"transport_ref {declared_refs!r} not checked: the sibling "
                    f"connector.json at {sibling} could not be parsed, so its "
                    "`transports` could not be read. Fix the error reported "
                    "above and re-run."))
            else:
                findings.append(finding(
                    "endpoint-transport-ref", "warning", "/",
                    f"transport_ref {declared_refs!r} not checked: no sibling "
                    "connector.json was reachable from this document's path, so "
                    "its `transports` could not be read. Validate the connector "
                    "to resolve it."))
    if doc_path is not None:
        findings += endpoint_filename_findings(doc, doc_path.name)
    return findings


def _validate_database_endpoint(doc: Any, doc_path: Path | None, schema_url: str | None = None) -> list[dict]:  # skipcq: PYL-W0613 — uniform registered-validator signature
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


def _validate_type_map(doc: Any, doc_path: Path | None, schema_url: str | None = None) -> list[dict]:
    # Direction from the filename; fall back to the --schema-url hint (write
    # vs read) when the filename is ambiguous, before defaulting to read.
    by_name = doc_path.name if doc_path is not None else ""
    if by_name == _WRITE_MAP_FILENAME or (
        by_name != _READ_MAP_FILENAME and isinstance(schema_url, str) and "type-map-write" in schema_url
    ):
        direction = "write"
    else:
        direction = "read"
    findings = _type_map_findings(doc, direction)
    if direction == "read" and doc_path is not None and doc_path.name not in (
        _READ_MAP_FILENAME, _WRITE_MAP_FILENAME
    ) and not (isinstance(schema_url, str) and "type-map-read" in schema_url):
        findings.append(finding("type-map-rule", "warning", "/",
                                f"rule direction defaulted to 'read': filename {doc_path.name!r} is "
                                f"neither {_READ_MAP_FILENAME!r} nor {_WRITE_MAP_FILENAME!r} "
                                "(pass --schema-url to disambiguate)."))
    return findings


def _validate_kindless_connector(doc: Any, doc_path: Path | None, schema_url: str | None = None) -> list[dict]:  # skipcq: PYL-W0613 — uniform registered-validator signature
    # A dict carrying connector sentinels but no `kind` is a connector missing
    # its discriminator — hand it to the model so the missing `kind` is reported
    # (rather than silently passing as "unrecognized").
    return _model_findings(doc, _CONNECTOR_ADAPTER)


# Registration order mirrors the original dispatch precedence: connector,
# api-endpoint, database-endpoint, type-map (any JSON array), then the
# kindless-connector fallback. `_core._dispatch` runs these in order and falls
# through to the generic "unrecognized document" verdict if none match.
register_kind(is_connector_doc, _validate_connector)
register_kind(is_api_endpoint_doc, _validate_api_endpoint)
register_kind(is_database_endpoint_doc, _validate_database_endpoint)
register_kind(lambda doc: isinstance(doc, list), _validate_type_map)
register_kind(
    lambda doc: isinstance(doc, dict) and any(k in doc for k in _CONNECTOR_SENTINELS),
    _validate_kindless_connector,
)
