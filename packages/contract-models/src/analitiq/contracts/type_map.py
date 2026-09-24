"""Type-map contract model — the on-disk `type-map.json` a connector or a
connection ships under its `definition/`.

The file is a top-level JSON object `{$schema, read, write}`: each direction's
rules sit under the key naming it, as an ordered, non-empty array of
`{match, native_type, arrow_type}` rules (first match wins). A map carries the
directions it covers and omits the rest. The directions share the rule
shape but invert which key is the *matcher* and which is *rendered*:

- **read**  (`native_type → arrow_type`): match on `native_type`, render
  `arrow_type` (the rendered side is the Apache Arrow vocabulary).
- **write** (`arrow_type → native_type`): match on `arrow_type`, render
  `native_type` (the rendered side is free-form dialect DDL).

Source of truth for the published `type-map` JSON Schema and the connector
validator (which validates via `model_validate`). Only *error*-level rules live
here — things that make a document invalid. Advisory quality checks that the
contract tolerates (duplicate rules, read patterns spelling a lowercase literal,
regex natives spelling a container that renders a scalar, write-vocabulary
coverage gaps) are not contract violations and stay in the validator as
warnings.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict, get_args

from pydantic import (
    ConfigDict, Field, PrivateAttr, StringConstraints, ValidationError, model_validator,
)

from analitiq.contracts.arrow_grammar import (
    CONTAINER_CANONICAL_HEADS as _CONTAINER_CANONICAL_HEADS,
    TEMPLATE_DUMMY_SUBSTITUTIONS,
    validate_cross_params,
    validate_template_bounds,
)
from analitiq.contracts.endpoints import ARROW_TYPE_PATTERN
from analitiq.contracts.shared.common import DocumentText, StrictModel, schema_url_for
from analitiq.contracts.shared.re2_dialect import compile_re2
from analitiq.contracts.shared.rules import violation

#: Schema URL declared by every `type-map.json` document — always a
#: standalone file (never an embedded API payload), so the field is required,
#: unlike the optional `$schema` on dual-use models such as `ConnectionAuthored`.
TYPE_MAP_SCHEMA_URL = schema_url_for("type-map")

# A literal `arrow_type` uses the SAME strict Arrow vocabulary the endpoint
# `arrow_type` does (`ARROW_TYPE_PATTERN`, incl. the `Json`/`Object`/`List`
# markers): one source of truth, so a type map cannot render an Arrow type an
# endpoint would reject. The pattern requires parameterized types to carry their
# parameters (`Timestamp(MICROSECOND)`, not bare `Timestamp`).
_ARROW_TYPE_RE = re.compile(ARROW_TYPE_PATTERN)

# `${name}` render-side substitution (empty `${}` captured too, flagged below).
_PLACEHOLDER_RE = re.compile(r"\$\{([^}]*)\}")

# Canonical read-match normalization — one collapsing-whitespace regex reused.
_NATIVE_WS_RUN = re.compile(r"\s+")


def normalize_native_type(value: str) -> str:
    r"""Canonical normalization for type-map READ matching, platform-wide.

    Trim leading/trailing whitespace, collapse internal whitespace runs to a
    single space, then uppercase. This is the single source of truth every
    read-side consumer normalizes with, so a native resolves identically
    wherever it is matched:

    * **exact** rules — applied SYMMETRICALLY to the rule's `native_type` (at
      map-build time) and to the probed native (at lookup), so a rule authored
      `varchar` or `CHARACTER  VARYING` still matches `VARCHAR` /
      `character varying`. SQL type names are case-insensitive and drivers
      report inconsistent casing/spacing, so verbatim matching is a
      silent-miss footgun.
    * **regex** rules — applied to the probed native only; the pattern is
      authored verbatim (uppercasing it would corrupt classes like `\d`→`\D`).

    Consumers that cannot import this module (the engine's in-container reader,
    the alq-common `alq.db.type_map` layer) MIRROR this body byte-for-byte
    under a conformance test rather than diverging.
    """
    return _NATIVE_WS_RUN.sub(" ", value.strip()).upper()


def _validate_type_map_arrow_type(value: str) -> None:
    """Validate an `arrow_type` value against the strict Arrow pattern.

    A literal is `fullmatch`ed directly. A templated `arrow_type`
    (`Decimal128(${p}, ${s})`, `Timestamp(${unit})`) has each `${...}`
    substituted with a dummy first, then `fullmatch`ed — so the whole SHAPE is
    validated (a scalar carrying parameters like `Utf8(${x})`, trailing garbage,
    or a typo'd head all fail), not just the head. Several dummies are tried
    (one per parameter grammar — a number for int positions plus one keyword
    per unit family, derived from the vendored engine grammar); a templated
    value is valid if ANY substitution yields a real Arrow type.
    Substitutions are parameter-positional, so one placeholder per parameter
    (`Decimal128(${p}, ${s})`, not `Decimal128(${p})`)."""
    if _PLACEHOLDER_RE.search(value):
        candidates = [_PLACEHOLDER_RE.sub(d, value)
                      for d in TEMPLATE_DUMMY_SUBSTITUTIONS]
    else:
        candidates = [value]
    # `fullmatch`, not `match`, so a trailing newline (which Python `$` allows)
    # is rejected — matching the endpoint model's arrow_type check.
    if not any(_ARROW_TYPE_RE.fullmatch(c) for c in candidates):
        raise ValueError(f"arrow_type {value!r} is not a valid Arrow type")
    # Pattern first, cross-parameter bound second — same order as the endpoint
    # model sites, so one mistake gets one diagnosis (a shape error never
    # surfaces as a scale complaint) and the check only ever sees
    # pattern-valid ASCII digits. `validate_cross_params` skips any position
    # holding a placeholder rather than digits, so it needs no guard here; what
    # a placeholder CAN become is decided against the native matcher instead,
    # by `validate_template_bounds` at the rule level.
    validate_cross_params(value)


# ---------------------------------------------------------------------------
# Matchers: compiled and matched in RE2, the dialect the contract fixes for them
# ---------------------------------------------------------------------------

# Locates a spelling that may open a named group; RE2 decides whether it does.
# A lookahead, so a spelling that opens nothing cannot consume the one after it.
_NAMED_GROUP_SPELLING = re.compile(r"(?=(?P<spelling>\(\?(?P<python>P?)<(?P<name>[^>]*)>))")


@dataclass(frozen=True, slots=True)
class CompiledMatcher:
    """A type-map matcher compiled in RE2."""

    # The binding publishes no type for a compiled pattern.
    regex: Any

    def fullmatch(self, subject: str) -> Any:
        """RE2's match of the whole `subject`, or None.

        RE2 reads UTF-8, so a subject with no UTF-8 encoding (a lone surrogate,
        which JSON can spell) is matched by nothing."""
        try:
            return self.regex.fullmatch(subject)
        except UnicodeEncodeError:
            return None


def _group_opened_at(pattern: str, spelling: re.Match[str], names: Any) -> int | None:
    """The number of the group RE2 opens at `spelling`, None when the spelling
    opens none (it sits in a class, a quote or after an escape): renaming it
    renames a group exactly when it opens one."""
    probe = "probe"
    while probe in names:
        probe += "_"
    renamed = f"{pattern[:spelling.start()]}(?<{probe}>{pattern[spelling.end('spelling'):]}"
    try:
        return compile_re2(renamed).groupindex.get(probe)
    except ValueError:
        return None


def compile_matcher(pattern: str) -> CompiledMatcher:
    """Compile a type-map matcher in RE2, the dialect the rule is matched in.

    ValueError carrying RE2's own parse error when RE2 refuses the pattern, and
    for what RE2 accepts and the contract does not: a named group spelled
    `(?P<name>…)`, and a name given to more than one group, which RE2 binds to
    the first only, so a match through a later one captures nothing under it."""
    try:
        regex = compile_re2(pattern)
    except ValueError as refusal:
        raise ValueError(f"matcher {refusal}") from refusal
    named: set[str] = set()
    for spelling in _NAMED_GROUP_SPELLING.finditer(pattern):
        if not _group_opened_at(pattern, spelling, regex.groupindex):
            continue
        if spelling["python"]:
            raise ValueError(
                "matcher spells a named group '(?P<name>…)'; the contract "
                "takes only '(?<name>…)'"
            )
        if spelling["name"] in named:
            raise ValueError(f"matcher gives the name {spelling['name']!r} to more than one group")
        named.add(spelling["name"])
    return CompiledMatcher(regex)


def _named_group_source(pattern: str, name: str) -> str:
    """The `(?<name>…)` group's sub-pattern: the source from the opener RE2
    binds the name to up to the first `)` where the source compiles as a group
    of its own. A `)` before that sits in a class, a quote, an escape or a
    nested group, so the source cut there does not compile. Inline flags set
    outside the group are not carried.

    KeyError when the matcher has no group of that name. RuntimeError when the
    group cannot be located in a matcher RE2 compiled: a fault here, never the
    author's, so it must not surface as the ValueError a rule reports."""
    names = compile_re2(pattern).groupindex
    number = names[name]
    opener = next((
        spelling for spelling in _NAMED_GROUP_SPELLING.finditer(pattern)
        if spelling["name"] == name and _group_opened_at(pattern, spelling, names) == number
    ), None)
    if opener is None:
        raise RuntimeError(f"RE2 names group {name!r} but no spelling in {pattern!r} opens it")
    close = opener.end("spelling")
    while True:
        close = pattern.find(")", close)
        if close < 0:
            raise RuntimeError(f"no `)` in {pattern!r} closes group {name!r} as RE2 does")
        source = pattern[opener.end("spelling"):close]
        try:
            compile_re2(f"(?:{source})")
        except ValueError:
            close += 1
            continue
        return source


def _capture_language(native: str, name: str, probes: tuple[str, ...]) -> frozenset[str]:
    """Which of `probes` the native's `(?<name>…)` capture can match.

    The capture is interrogated in isolation: surrounding context that would
    further constrain it is ignored, and inline flags set outside it are
    dropped, which can narrow it."""
    capture = compile_re2(f"(?:{_named_group_source(native, name)})")
    return frozenset(probe for probe in probes if capture.fullmatch(probe))


def _native_is_schemaless_container(literal_text: str) -> bool:
    """Best-effort, DB-agnostic detection of a structured/container native from
    its SYNTAX — never a vendor type-name list. Container natives are recognised
    by shape: angle-bracket parameterization (`array<int>`, `struct<...>`,
    `map<k, v>`) or a SQL array suffix (`integer[]`), read from the native's
    literal characters. Bare vendor scalars-for-JSON (`JSONB`, `VARIANT`, …) are
    intentionally not special-cased; if their structure matters the author
    writes it with `<...>` or `[]`."""
    return ("<" in literal_text and ">" in literal_text) or literal_text.endswith("[]")


def _guard_container_not_collapsed(native_type: str, literal_text: str, arrow_type: str) -> None:
    """A schemaless/structured native_type must not resolve to a scalar Arrow type
    (which would silently drop the value's structure). Read direction only.
    `literal_text` is what container syntax is read from: the native itself for
    an exact rule, a reading of the matcher's literal characters for a regex one."""
    if _native_is_schemaless_container(literal_text):
        head = _arrow_type_head(arrow_type)
        if head and head not in _CONTAINER_CANONICAL_HEADS:
            raise ValueError(
                f"native_type {native_type!r} is a schemaless/structured container but "
                f"resolves to scalar arrow_type {arrow_type!r}; map it to a container "
                "Arrow type (`Json`, or `Object`/`List` for endpoint narrowings)"
            )


def _arrow_type_head(arrow_type: str) -> str:
    """Leading PascalCase Arrow type name (empty if it opens with `${…}`)."""
    m = re.match(r"\s*([A-Za-z][A-Za-z0-9]*)", arrow_type)
    return m.group(1) if m else ""


def _validate_render_placeholders(render: str) -> None:
    """Reject malformed `${...}` in a render template: an empty `${}` or an
    unclosed `${` (missing `}`). Applies to any render that may carry `${name}`
    substitutions — a regex render, or an exact WRITE rule's `native_type` DDL."""
    if any(not name.strip() for name in _PLACEHOLDER_RE.findall(render)):
        raise ValueError(f"render value {render!r} contains an empty ${{}} placeholder")
    if "${" in _PLACEHOLDER_RE.sub("", render):
        raise ValueError(f"render value {render!r} has an unclosed '${{' (missing '}}')")


# An EXACT rule's `arrow_type` is a literal Arrow type. `ARROW_TYPE_PATTERN` both
# validates the Arrow grammar AND forbids `${…}` (the grammar admits no `$`), so
# this one field constraint replaces the runtime Arrow-vocabulary + no-template
# checks — and, unlike a `@model_validator`, it publishes into the generated JSON
# Schema, so external consumers enforce it too. (A REGEX rule's `arrow_type` is a
# render template or matcher and is validated at runtime instead — see below.)
_ExactArrowType = Annotated[str, Field(min_length=1, pattern=ARROW_TYPE_PATTERN)]
_NonEmptyStr = Annotated[str, Field(min_length=1)]


class _TypeMapRuleBase(StrictModel):
    """Shared fields for every `{match, native_type, arrow_type}` rule. `match` is the
    discriminator selecting the exact/regex variant."""

    native_type: _NonEmptyStr
    arrow_type: _NonEmptyStr


class TypeMapReadExactRule(_TypeMapRuleBase):
    """Read exact rule: literal `native_type` matches, literal Arrow `arrow_type` renders."""

    match: Literal["exact"]
    arrow_type: _ExactArrowType

    @model_validator(mode="after")
    def _check(self) -> "TypeMapReadExactRule":
        # Cross-parameter bounds the field pattern cannot express
        # (Decimal scale <= precision).
        validate_cross_params(self.arrow_type)
        try:
            _guard_container_not_collapsed(self.native_type, self.native_type, self.arrow_type)
        except ValueError as detail:
            raise violation("RULE-TMAP-001", "read-exact-container-collapsed", str(detail)) from None
        return self


class TypeMapReadRegexRule(_TypeMapRuleBase):
    """Read regex rule: RE2 `native_type` matches, Arrow `arrow_type` render template
    (its `${name}` placeholders draw from the `native_type`'s named captures)."""

    match: Literal["regex"]

    @model_validator(mode="after")
    def _check(self) -> "TypeMapReadRegexRule":
        # `arrow_type` is a (possibly templated) Arrow type — validate its shape.
        try:
            _validate_type_map_arrow_type(self.arrow_type)
        except ValueError as detail:
            raise violation("RULE-TMAP-006", "read-regex-arrow-type-invalid", str(detail)) from None
        try:
            compiled = compile_matcher(self.native_type)
        except ValueError as detail:
            raise violation("RULE-TMAP-005", "read-regex-native-type-not-ecma", str(detail)) from None
        try:
            _validate_render_placeholders(self.arrow_type)
        except ValueError as detail:
            raise violation("RULE-TMAP-007", "read-regex-malformed-placeholder", str(detail)) from None

        # Every `${name}` in the `arrow_type` render must name a `native_type` capture.
        capture_names = set(compiled.regex.groupindex.keys())
        placeholders = _PLACEHOLDER_RE.findall(self.arrow_type)
        for name in placeholders:
            if name not in capture_names:
                raise violation(
                    "RULE-TMAP-003", "render-references-unknown-capture",
                    f"render references ${{{name}}} but the matcher has no matching "
                    f"(?<{name}>…) capture group"
                )
        # Reverse correspondence: a native that CAPTURES parameters must not map to
        # a FULLY hardcoded parameterized arrow_type that discards them (every match
        # would collapse to that one by-example constant). A literal parameterized
        # Arrow type carries `(...)`; match-and-discard is expressed with a
        # non-capturing group `(?:…)`. Scope: the detector keys on `(`
        # — paren-parameterized scalars (`Decimal128(…)`, `Timestamp(…)`).
        # Deliberately out of scope: a partially-templated arrow_type
        # (`Decimal128(${p}, 9)`, guarded by `not placeholders`) and a capture
        # discarded into a hardcoded nested `<…>`/`[…]` arrow_type (governed by the
        # schemaless-container rule instead).
        if capture_names and not placeholders and "(" in self.arrow_type:
            raise violation(
                "RULE-TMAP-004", "capture-discarded-by-hardcoded-arrow-type",
                f"native_type {self.native_type!r} captures {sorted(capture_names)} but arrow_type "
                f"{self.arrow_type!r} is a hardcoded parameterized type that discards them; "
                "reference the captures (e.g. `Decimal128(${p}, ${s})`) or use a "
                "non-capturing group `(?:…)` if the parameter is intentionally dropped"
            )
        # Last, because it presumes every `${name}` resolves to a real capture:
        # what the rule RENDERS must be an Arrow type whatever the native_type matches,
        # and a templated position carries no value of its own — so the only
        # thing that decides it is the capture the render draws from.
        try:
            validate_template_bounds(
                self.arrow_type,
                lambda name, probes: _capture_language(self.native_type, name, probes),
            )
        except ValueError as detail:
            raise violation("RULE-TMAP-010", "capture-cannot-satisfy-template-bound", str(detail)) from None
        return self


class TypeMapWriteExactRule(_TypeMapRuleBase):
    """Write exact rule: literal Arrow `arrow_type` matches, `native_type` DDL renders."""

    match: Literal["exact"]
    arrow_type: _ExactArrowType

    @model_validator(mode="after")
    def _check(self) -> "TypeMapWriteExactRule":
        # Cross-parameter bounds the field pattern cannot express
        # (Decimal scale <= precision).
        try:
            validate_cross_params(self.arrow_type)
        except ValueError as detail:
            raise violation("RULE-TMAP-008", "write-exact-cross-param-bound", str(detail)) from None
        # A write `native_type` render may carry `${length}` DDL hints — they must be
        # syntactically valid (no empty `${}` / unclosed `${`).
        try:
            _validate_render_placeholders(self.native_type)
        except ValueError as detail:
            raise violation("RULE-TMAP-008", "write-exact-malformed-placeholder", str(detail)) from None
        return self


class TypeMapWriteRegexRule(_TypeMapRuleBase):
    """Write regex rule: RE2 `arrow_type` matches, `native_type` DDL render template.
    `arrow_type` is the matcher here, so it is NOT held to the Arrow vocabulary."""

    match: Literal["regex"]

    @model_validator(mode="after")
    def _check(self) -> "TypeMapWriteRegexRule":
        try:
            compile_matcher(self.arrow_type)
        except ValueError as detail:
            raise violation("RULE-TMAP-009", "write-regex-arrow-type-not-ecma", str(detail)) from None
        try:
            _validate_render_placeholders(self.native_type)
        except ValueError as detail:
            raise violation("RULE-TMAP-009", "write-regex-malformed-placeholder", str(detail)) from None
        return self


# `match`-discriminated unions: the exact branch carries the Arrow `pattern` on
# `arrow_type` (published into the JSON Schema); the regex branch keeps its
# runtime-only render/capture checks. Each direction renders a `oneOf` with a
# `match` discriminator, so external validators reject exactly what the model does.
TypeMapReadRule = Annotated[
    TypeMapReadExactRule | TypeMapReadRegexRule,
    Field(discriminator="match"),
]
TypeMapWriteRule = Annotated[
    TypeMapWriteExactRule | TypeMapWriteRegexRule,
    Field(discriminator="match"),
]


TypeMapDirection = Literal["read", "write"]
TYPE_MAP_DIRECTIONS = get_args(TypeMapDirection)


class TypeMapDoc(StrictModel):
    """`type-map.json`: a `$schema` and, keyed by direction, the ordered rule
    list for each direction the map covers. At least one direction is
    present."""

    # A branch per direction, each requiring its section present AND non-null,
    # so the published schema refuses exactly the documents
    # `_at_least_one_direction` refuses.
    model_config = ConfigDict(json_schema_extra={"anyOf": [
        {"required": [d], "properties": {d: {"not": {"type": "null"}}}}
        for d in TYPE_MAP_DIRECTIONS
    ]})

    schema_url: Literal[TYPE_MAP_SCHEMA_URL] = Field(
        ...,
        alias="$schema",
        description="Schema URL declared by every `type-map.json` document.",
    )
    read: Annotated[list[TypeMapReadRule], Field(min_length=1)] | None = Field(
        default=None,
        description="Read rules: each matches a `native_type` and renders an `arrow_type`.",
    )
    write: Annotated[list[TypeMapWriteRule], Field(min_length=1)] | None = Field(
        default=None,
        description="Write rules: each matches an `arrow_type` and renders a `native_type`.",
    )

    @model_validator(mode="after")
    def _at_least_one_direction(self) -> "TypeMapDoc":
        if all(getattr(self, d) is None for d in TYPE_MAP_DIRECTIONS):
            raise violation(
                "RULE-TMAP-023", "type-map-no-section",
                "a type map declares a rule list under at least one of `read` or `write`")
        return self


# The side a direction's rule matches on, and the side it renders.
_MATCH_AND_RENDER_KEYS = {"read": ("native_type", "arrow_type"), "write": ("arrow_type", "native_type")}


def resolve_type(value: str, rules: list, direction: TypeMapDirection) -> str | None:
    r"""`value` rendered by the first of `rules` matching it, or None when none does.

    Read matching normalizes the probe with `normalize_native_type`, and an
    `exact` rule's `native_type` the same way; a `regex` matcher is never
    normalized, since uppercasing it would turn `\d` into `\D`. Write matching
    compares the `arrow_type` as authored. A regex match substitutes each
    `${name}` in the render with its capture.

    `rules` need not have passed the model, so the validator can resolve over a
    map it is still grading. A rule that is not an object, lacks a string on
    either side, or carries a regex matcher the contract refuses renders
    nothing; any other rule renders as authored."""
    matcher_key, render_key = _MATCH_AND_RENDER_KEYS[direction]
    normalize = normalize_native_type if direction == "read" else None
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


# Coarse guards against an unbounded request, set far above what one
# resolution needs.
MAX_RESOLVE_TYPES = 2000
MAX_RESOLVE_MAPS = 16
MAX_RESOLVE_TYPE_LENGTH = 1024

ResolvableType = Annotated[str, StringConstraints(min_length=1, max_length=MAX_RESOLVE_TYPE_LENGTH)]


class ResolveTypesRequest(StrictModel):
    """A request to translate types through type maps: the direction, the types
    in that direction's vocabulary, and the maps as file text in precedence
    order.

    Every map must be a type map, and at least one must carry the direction's
    section. A map without it contributes no rules."""

    direction: TypeMapDirection = Field(
        ...,
        description=(
            "`read` translates provider native types to Arrow types; `write` "
            "translates Arrow types to native types."
        ),
    )
    types: Annotated[list[ResolvableType], Field(min_length=1, max_length=MAX_RESOLVE_TYPES)] = Field(
        ...,
        description="The types to translate, spelled as the direction's matching side spells them.",
    )
    maps: Annotated[list[DocumentText], Field(min_length=1, max_length=MAX_RESOLVE_MAPS)] = Field(
        ...,
        description=(
            "`type-map.json` file texts, highest precedence first: a type resolves "
            "through the first map with a rule matching it."
        ),
    )

    _rules: list = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def _maps_are_type_maps_carrying_the_direction(self) -> "ResolveTypesRequest":
        sections = []
        for index, text in enumerate(self.maps):
            try:
                TypeMapDoc.model_validate_json(text)
            except ValidationError as refusal:
                raise ValueError(f"maps[{index}] is not a type map: {refusal}") from refusal
            sections.append(json.loads(text).get(self.direction))
        # Every type would come back unresolved, which reads as a vocabulary to
        # cover rather than the missing section it is.
        if all(section is None for section in sections):
            raise ValueError(f"no map carries a {self.direction!r} section")
        self._rules = [rule for section in sections if section for rule in section]
        return self


class ResolvedTypes(TypedDict):
    """Each requested type mapped to its translation, or None when no rule
    renders it; `gaps` lists the None ones in request order."""

    resolved: dict[str, str | None]
    gaps: list[str]


def resolve_types(request: ResolveTypesRequest) -> ResolvedTypes:
    """Translate every type in `request` through its maps, earliest map first."""
    types = list(dict.fromkeys(request.types))
    resolved = {t: resolve_type(t, request._rules, request.direction) for t in types}
    return {"resolved": resolved, "gaps": [t for t in types if resolved[t] is None]}
