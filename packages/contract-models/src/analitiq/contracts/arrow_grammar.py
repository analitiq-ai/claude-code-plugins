"""Engine-published Arrow type grammar — the vendored capability manifest.

The set of canonical Arrow type families the platform executes end-to-end is a
**capability surface**: only the engine can make a statement about it true, so
the engine owns it. analitiq-core publishes the vocabulary as a
generated, versioned artifact alongside its conversion matrix:

    https://schemas.analitiq.ai/arrow-type-grammar/latest.json          (pointer)
    https://schemas.analitiq.ai/arrow-type-grammar/v{V}/arrow_type_grammar.json
    https://schemas.analitiq.ai/conversion-matrix/latest.json           (pointer)
    https://schemas.analitiq.ai/conversion-matrix/v{V}/conversion_matrix.json

This module vendors ONE pinned, immutable version of the grammar manifest
(`arrow_type_grammar.json`, byte-identical to the published object) and derives
from it everything the contract used to restate by hand:

- the `ARROW_TYPE_PATTERN` alternatives (re-exported by `endpoints.py`),
- the published `canonical-types.json` `$defs` (via `scripts/render_schemas.py`),
- the container-head set type-map validation reasons over,
- the dummy substitutions templated type-map canonicals are checked with.

Both artifacts self-declare their version in a top-level `version` key
(grammar v1.1.0 and matrix v2.0.0 on). The pin below still states
the version — it is what builds the URL to fetch, so it cannot be derived from
the thing it fetches — but the guards now assert the object's OWN version
against it rather than trusting the path it asked for.

The pin (version + sha256, both manifests) is stated here once. Guards:

- `tests/unit/test_arrow_grammar.py` re-hashes the vendored file against the
  pin and checks its self-declared version — offline, so an edited or swapped
  vendored copy fails everywhere;
- `scripts/check_engine_grammar_pin.py` (CI) fetches the pinned published
  object, byte-compares it against the vendored copy, asserts the MATRIX's
  self-declared version directly and the GRAMMAR's via the vendored copy those
  bytes are identical to, cross-checks the conversion-matrix family keys, and
  reports when the engine has published a newer version than the pin.

Updating the pin:

- **Grammar** — replace the vendored file with the newly published object and
  bump `ENGINE_GRAMMAR_VERSION` / `ENGINE_GRAMMAR_SHA256` together (the sha is
  `sha256` of the published bytes; `latest.json` also states it).
- **Matrix** — nothing is vendored, so bump `CONVERSION_MATRIX_VERSION` /
  `CONVERSION_MATRIX_SHA256` from the published `latest.json`. A MAJOR bump
  here can be an envelope/shape change rather than a vocabulary change (v2.0.0
  moved the grid under `conversions`), which means the guard's reader may need
  teaching before the pin can move.
- Then re-render the schemas (`render_schemas.py canonical-types`, plus any
  affected resource) and re-run the plugin doc generator.

Per the re-add policy a family appears here only after the engine executes it:
the engine work ships first, and the contract picks it up by consuming the new
manifest version — never by hand-editing the vocabulary.

What stays contract-owned (authoring-profile policy, not engine facts):

- **Canonical spelling only.** Units are the uppercase Flatbuffers enum
  identifiers; the engine's tolerated short forms (`us`, `ms`, ...) are not
  authorable. Stricter-than-engine is always safe.
- **The IANA timezone regex approximation.** The engine validates real zone
  names against the tzdb at runtime, which no regex can restate; the contract
  publishes a syntactic approximation plus the manifest's own
  `fixed_offset_pattern` verbatim.
- **The `${name}` template grammar** of type-map render rules — a contract
  feature the engine never sees.
- **The wire date-time profile.** The engine parses whatever its reader
  accepts; this is the subset a recorded sample's zone-awareness is read from,
  and a value outside it is read as no evidence rather than as no zone.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# The pin — the single place the vendored manifest versions are stated.
# ---------------------------------------------------------------------------

ENGINE_GRAMMAR_RESOURCE = "arrow-type-grammar"
ENGINE_GRAMMAR_VERSION = "1.1.0"
ENGINE_GRAMMAR_SHA256 = (
    "43f434025ae9fcaf8609b0cd494c3a04cac6bb4abf0f1fe50caa590ffbbbc679"
)
ENGINE_GRAMMAR_FILENAME = "arrow_type_grammar.json"

# The conversion matrix is NOT vendored — the vocabulary needs only the grammar.
# The pin is recorded so the CI guard can (a) fetch the right object, (b) assert
# that object's own declared version, and (c) verify the two published artifacts
# agree — grammar families == the keys of the matrix's `conversions` grid — at
# the pinned versions.
CONVERSION_MATRIX_RESOURCE = "conversion-matrix"
CONVERSION_MATRIX_VERSION = "2.0.0"
CONVERSION_MATRIX_SHA256 = (
    "6a40da57330c435908973cb70aaa33d5af5e220dcb1bcaf55e29e365713bd072"
)
CONVERSION_MATRIX_FILENAME = "conversion_matrix.json"

#: Key holding the family->spec map inside the grammar manifest.
GRAMMAR_FAMILIES_KEY = "families"
#: Key holding the family x family grid inside the conversion matrix.
MATRIX_CONVERSIONS_KEY = "conversions"
#: Key each grid cell carries its conversion mode under.
MATRIX_CELL_MODE_KEY = "mode"
#: The mode vocabulary is ENGINE-OWNED and deliberately not restated in this
#: repo; this one value is named because it carries the single universal
#: invariant the engine generates unconditionally and pins with its own tests:
#: every diagonal cell (family to itself) declares the identity mode. Guards
#: assert exactly that and nothing about any off-diagonal mode value.
MATRIX_IDENTITY_MODE = "identity"
#: The parameter kind naming a family's timezone position. The manifest states
#: which families carry one, so nothing here decides it: a family the engine
#: later gives a zone to is read correctly without an edit.
TIMEZONE_PARAM_KIND = "timezone"
#: Key each artifact stamps its own version under. Both artifacts
#: self-declare from grammar v1.1.0 / matrix v2.0.0 on, which is what lets a
#: consumer assert the version it got rather than trusting the URL it asked for.
ARTIFACT_VERSION_KEY = "version"

_GRAMMAR_PATH = Path(__file__).with_name(ENGINE_GRAMMAR_FILENAME)


def load_grammar() -> dict[str, Any]:
    """The vendored manifest, parsed. Kept as a function so guards can re-read
    the file (hash checks compare bytes, not this parse)."""
    return json.loads(_GRAMMAR_PATH.read_text(encoding="utf-8"))


def _unusable(reason: str) -> RuntimeError:
    """The one remediation message for a vendored file we cannot derive from.

    Returns the error for the caller to `raise` (optionally `from` a cause),
    so every rejection below reads as a one-liner and the remediation text
    exists once.
    """
    return RuntimeError(
        f"vendored engine grammar {_GRAMMAR_PATH} is unusable ({reason}); "
        f"re-vendor the published {ENGINE_GRAMMAR_RESOURCE}/"
        f"v{ENGINE_GRAMMAR_VERSION} object (see this module's docstring for "
        "the pin-update procedure)"
    )


def _load_families() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Parse the vendored manifest and pull the vocabulary out of its envelope.

    Every `analitiq.contracts.*` import passes through here, so a vendored file
    this module cannot derive from must name its remediation rather than
    surface as a bare exception three imports deep. Checked here:

    - unreadable / not JSON / not an object;
    - a missing or renamed `families` key, which would otherwise be a bare
      `KeyError`. Not hypothetical — the sibling conversion-matrix artifact did
      exactly this rename in its v2.0.0 (bare grid -> `conversions`), so a
      future grammar major could relocate `families` the same way;
    - an EMPTY `families` map, which parses, imports, and derives
      `ARROW_TYPE_PATTERN == "^(?:)$"` — a contract accepting no canonical type
      at all. Every derivation below is a comprehension over `FAMILIES`, so
      emptiness propagates silently instead of failing;
    - a family whose spec is not an object, which would otherwise surface as
      `AttributeError: 'str' object has no attribute 'get'` from whichever
      derivation happened to run first.

    NOT checked here: the internals of a well-formed spec (param `kind`, `min`,
    `allowed`, …). Those are the pattern generators' domain — `family_pattern`
    and `_param_literal_pattern` already fail loudly and self-describingly on
    them, and duplicating that validation would be a second place to update
    when the manifest grammar grows.
    """
    try:
        grammar: dict[str, Any] = load_grammar()
    except OSError as exc:
        raise _unusable(f"cannot read the file: {exc}") from exc
    except ValueError as exc:  # JSONDecodeError/UnicodeDecodeError are both this
        raise _unusable(f"not valid JSON: {exc}") from exc

    if not isinstance(grammar, dict):
        raise _unusable(f"top level is {type(grammar).__name__}, expected object")
    if GRAMMAR_FAMILIES_KEY not in grammar:
        raise _unusable(f"no `{GRAMMAR_FAMILIES_KEY}` key")
    families = grammar[GRAMMAR_FAMILIES_KEY]
    if not isinstance(families, dict):
        raise _unusable(
            f"`{GRAMMAR_FAMILIES_KEY}` is {type(families).__name__}, expected object"
        )
    if not families:
        raise _unusable(f"`{GRAMMAR_FAMILIES_KEY}` is empty")
    bad = sorted(n for n, spec in families.items() if not isinstance(spec, dict))
    if bad:
        raise _unusable(f"family specs are not objects: {bad}")
    return grammar, families


#: The whole manifest — an envelope. v1.0.0 already carried the vocabulary
#: under `families`; v1.1.0 added a sibling `version` alongside it.
GRAMMAR: dict[str, Any]
#: family name -> param spec, exactly as the engine publishes it. Read by key,
#: so envelope siblings never reach the derivations below.
FAMILIES: dict[str, dict[str, Any]]
GRAMMAR, FAMILIES = _load_families()

# ---------------------------------------------------------------------------
# Contract-owned profile fragments (see module docstring for why these are
# policy, not restated engine facts).
# ---------------------------------------------------------------------------

#: A wire value in ISO-8601 date-time form — the one sample shape whose
#: zone-awareness can be decided by reading it. Contract-owned authoring
#: profile, not an engine fact: the engine parses what its reader accepts, and
#: this is the subset a document's recorded sample is graded on. Anything else
#: a provider sends (a date with no time, an epoch number, a provider-specific
#: spelling) carries no answer, and the reader below says so rather than
#: inventing one.
#: The positions are captured, because whether a sample is a real moment is
#: read off them: a shape match alone accepts a thirteenth month, and a `Z` on
#: a date that does not exist would otherwise read as evidence of a zone.
_WIRE_DATETIME_PATTERN = (
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})[Tt ]"
    r"(?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2})(?:[.,]\d+)?)?"
    r"(?P<zone>[Zz]|[+-]\d{2}(?::?\d{2})?)?"
)
#: ASCII, because `\d` is Unicode-aware and `int()` accepts what it matches:
#: without this, a value spelled in Arabic-Indic numerals reads as a real
#: zoned wire timestamp and decides a declaration. RFC 3339 spells its
#: productions in ASCII digits, and a value outside that grammar is one this
#: profile has no answer about rather than one it grades.
_WIRE_DATETIME_RE = re.compile(f"^{_WIRE_DATETIME_PATTERN}$", re.ASCII)
#: The largest seconds value the profile reads as a real one. 60 is a
#: leap second (RFC 3339 §5.6), which a wire value can carry and no
#: `datetime` can hold.
_MAX_WIRE_SECOND = 60
#: `time-hour = 2DIGIT ; 00-23` from the same RFC 3339 §5.6 grammar the
#: seconds bound above is read off. It bounds the clock hour AND the offset
#: hour, because the grammar spells both with that one production — and
#: reading one position out of it while exceeding the next would make the
#: profile's source a matter of which line it happened to suit.
_MAX_WIRE_HOUR = 23
#: `time-minute = 2DIGIT ; 00-59`, likewise for both positions.
_MAX_WIRE_MINUTE = 59

#: `${name}` placeholder of type-map render templates. Must be a valid
#: identifier, matching the native capture-group naming it resolves from.
PLACEHOLDER_PATTERN = r"\$\{[A-Za-z_][A-Za-z0-9_]*\}"

# Syntactic approximation of an IANA zone name. Real membership is a tzdb
# lookup the engine performs at runtime; a regex can only gate the shape.
# `Etc/GMT±N` zones carry a `+`/`-` the identifier class deliberately excludes
# (it would swallow malformed offsets), so they get their own alternative.
# The `/` is escaped for compatibility with every ECMA-262 mode (the `v` flag
# rejects a bare `/` in a character class); Python and the `u`/no-flag modes
# treat `\/` identically to `/`.
_IANA_ZONE = r"[A-Za-z_][A-Za-z0-9_\/\-]*"
_ETC_GMT_ZONE = r"Etc\/GMT[+\-][0-9]{1,2}"


def _int_range_pattern(lo: int, hi: int | None) -> str:
    """Regex for a decimal integer literal in [lo, hi] (no leading zeros).

    Covers exactly the shapes the manifest uses today — `lo` 0 or 1 with `hi`
    None (unbounded), or 0 <= lo <= hi <= 99 bounded. Anything else fails
    loudly so a manifest widening is a visible decision, not a silent misparse.
    """
    if hi is None:
        if lo == 0:
            return r"(?:0|[1-9][0-9]*)"
        if lo == 1:
            return r"[1-9][0-9]*"
        raise ValueError(f"unsupported unbounded int range min={lo}")
    if not (0 <= lo <= hi <= 99):
        raise ValueError(f"unsupported int range [{lo}, {hi}]")
    parts: list[str] = []
    # Single-digit span.
    if lo <= 9:
        lo_d, hi_d = lo, min(hi, 9)
        parts.append(f"[{lo_d}-{hi_d}]" if lo_d != hi_d else str(lo_d))
    # Two-digit span, decomposed by decade.
    if hi >= 10:
        lo2 = max(lo, 10)
        lo_dec, hi_dec = lo2 // 10, hi // 10
        for dec in range(lo_dec, hi_dec + 1):
            d_lo = lo2 % 10 if dec == lo_dec else 0
            d_hi = hi % 10 if dec == hi_dec else 9
            if d_lo > d_hi:
                continue
            unit = f"[{d_lo}-{d_hi}]" if d_lo != d_hi else str(d_lo)
            parts.append(f"{dec}{unit}")
    # Merge adjacent full decades ([2-9] style) is not attempted — clarity over
    # minimality; the pattern is generated, never read for style.
    return "(?:" + "|".join(parts) + ")"


#: A position that admits no literal at all — the resolved bound is empty
#: (`hi < lo`). `(?!)` is the standard never-matching fragment.
_UNSATISFIABLE_POSITION = "(?!)"


def resolved_int_bounds(
    param: dict[str, Any],
    params: list[dict[str, Any]],
    literals: dict[str, str] | None = None,
) -> tuple[int, int | None]:
    """The `(min, max)` an int parameter position actually admits.

    A bound stated as a sibling param's name (`"max": "precision"`) resolves in
    two steps, widest last:

    1. **to the literal sibling actually present**, when `literals` carries one
       — `Decimal128(5, ${s})` admits a scale of at most 5, not of 38;
    2. otherwise to the referenced param's own numeric ceiling/floor — scale <=
       precision <= 38 means a literal scale above 38 is unsatisfiable for ANY
       precision, the satisfiable envelope the published pattern caps at.

    Step 1 is what a caller holding one concrete canonical string gets; step 2
    is all a family-level pattern can know, since it is generated once for
    every value of that family. `max` may resolve to None (unbounded).
    """
    literals = literals or {}
    resolved: list[int | None] = []
    for key in ("min", "max"):
        bound = param[key] if key == "min" else param.get(key)
        if isinstance(bound, str):
            ref = next((p for p in params if p["name"] == bound), None)
            if ref is None:
                # A dangling ref would otherwise silently disable the bound at
                # BOTH layers: unbounded pattern here, and a silent skip in
                # `validate_cross_params` (named.get(ref) is None).
                raise ValueError(
                    f"param {param['name']!r} bound references unknown "
                    f"sibling param {bound!r}"
                )
            sibling = literals.get(bound)
            if sibling is not None and sibling.isdigit():
                bound = int(sibling)
            else:
                bound = ref.get(key) if isinstance(ref.get(key), int) else None
        resolved.append(bound)
    lo, hi = resolved
    if lo is None:
        raise ValueError(f"param {param['name']!r} has no resolvable min bound")
    return lo, hi


def _param_literal_pattern(
    param: dict[str, Any],
    params: list[dict[str, Any]],
    literals: dict[str, str] | None = None,
) -> str:
    """Regex for one parameter position's LITERAL values, from its spec.

    `params` is the owning family's full param list and `literals` the literal
    arguments of the concrete canonical being validated, if any; together they
    resolve a cross-parameter bound — see `resolved_int_bounds`. The relation
    itself cannot live in a per-position regex (`validate_cross_params`
    enforces it on literals); what a regex can carry is the resolved ceiling,
    which is why `Decimal128(${p}, 99)` fails both the published template
    pattern and the runtime dummy check.
    """
    kind = param["kind"]
    if kind == "int":
        lo, hi = resolved_int_bounds(param, params, literals)
        if hi is not None and hi < lo:
            return _UNSATISFIABLE_POSITION
        return _int_range_pattern(lo, hi)
    if kind == "unit":
        return "(?:" + "|".join(param["allowed"]) + ")"
    if kind == TIMEZONE_PARAM_KIND:
        null = param["null_sentinel"]
        offset = param["fixed_offset_pattern"]  # manifest-owned, verbatim
        return f"(?:{null}|{_IANA_ZONE}|{_ETC_GMT_ZONE}|{offset})"
    raise ValueError(f"unknown param kind {kind!r}")


def family_pattern(name: str, *, templated: bool = False) -> str:
    """Unanchored regex fragment accepting family `name`'s canonical strings.

    With `templated=True`, every parameter position additionally accepts a
    `${name}` placeholder (the type-map render-template grammar).
    """
    spec = FAMILIES[name]
    params = spec.get("params")
    if not params:
        # Bare families: scalars, opaque `Json`, and the structural
        # authored-shape markers (`Object`/`List` — sibling `properties`/`items`
        # rules are model-layer, not string-vocabulary, concerns).
        return name
    # The per-piece optional wrapper below is only correct for TRAILING
    # optional params (the comma rides inside each non-first piece). A leading
    # or middle optional would silently generate a wrong grammar — an
    # all-optional multi-param list included: `\((?:A)?(?:\s*,\s*B)?\)` accepts
    # the malformed `(,B)`. Fail loudly instead, like every other unsupported
    # manifest shape. A SOLE optional param is fine (no comma exists).
    first_optional = next(
        (i for i, p in enumerate(params) if p.get("optional")), len(params)
    )
    if any(not p.get("optional") for p in params[first_optional:]) or (
        first_optional == 0 and len(params) > 1
    ):
        raise ValueError(
            f"family {name!r} has a non-trailing optional param; the pattern "
            "generator only supports trailing optionals after a required first "
            "param"
        )
    # Assembled back-to-front so each optional NESTS the ones after it —
    # `A(?:,B(?:,C)?)?`, never the product form `A(?:,B)?(?:,C)?`, which would
    # accept a string that skips a middle optional but supplies a later one.
    # Byte-identical to the flat form for zero or one optional (all of today's
    # manifest).
    body = ""
    for i in range(len(params) - 1, -1, -1):
        param = params[i]
        literal = _param_literal_pattern(param, params)
        arg = f"(?:{literal}|{PLACEHOLDER_PATTERN})" if templated else literal
        piece = (arg if i == 0 else rf"\s*,\s*{arg}") + body
        body = f"(?:{piece})?" if param.get("optional") else piece
    return name + r"\(" + body + r"\)"


# ---------------------------------------------------------------------------
# Derived vocabulary — consumed by endpoints.py / type_map.py / the renderer.
# ---------------------------------------------------------------------------

#: Deterministic family order (sorted; the alternation is fullmatch-anchored,
#: so order carries no semantics — it only keeps renders byte-stable).
FAMILY_NAMES: tuple[str, ...] = tuple(sorted(FAMILIES))

#: family -> strict (non-templated) regex fragment.
ARROW_TYPE_FRAGMENTS: dict[str, str] = {
    name: family_pattern(name) for name in FAMILY_NAMES
}

#: The one published arrow_type regex: every engine-executable canonical
#: spelling, nothing else. Anchored, fullmatch semantics.
ARROW_TYPE_PATTERN = "^(?:" + "|".join(ARROW_TYPE_FRAGMENTS.values()) + ")$"

#: Families that carry parameters (and therefore have a templated form).
PARAMETERIZED_FAMILY_NAMES: tuple[str, ...] = tuple(
    name for name in FAMILY_NAMES if FAMILIES[name].get("params")
)

#: Canonical container heads: the structural authored-shape markers plus the
#: opaque `Json` container. `Json` is listed by hand because the manifest does
#: not (yet) flag container-ness on bare families — worth upstreaming as an
#: explicit `container` flag; until then this is the one profile-side judgment
#: in the set.
CONTAINER_CANONICAL_HEADS: frozenset[str] = frozenset(
    {name for name, spec in FAMILIES.items() if spec.get("structural")} | {"Json"}
)

#: Dummy literals for validating templated canonicals: substituting each dummy
#: for every `${...}` must yield a real canonical for at least one dummy. "1"
#: resolves int positions; the first allowed unit of each unit param resolves
#: unit positions. Derived, so a manifest unit-vocabulary change re-derives it.
TEMPLATE_DUMMY_SUBSTITUTIONS: tuple[str, ...] = tuple(
    sorted(
        {"1"}
        | {
            param["allowed"][0]
            for spec in FAMILIES.values()
            for param in spec.get("params") or ()
            if param["kind"] == "unit"
        }
    )
)


#: The bound keys carrying a cross-parameter relation, each with the predicate
#: that holds when it is satisfied — called as `ok(referenced, own)`.
_CROSS_BOUNDS: tuple[tuple[str, Callable[[int, int], bool], str], ...] = (
    ("min", int.__le__, ">="),
    ("max", int.__ge__, "<="),
)

#: A canonical argument that is a bare `${name}` placeholder and nothing else.
_PLACEHOLDER_ARG_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: Any `${name}` occurrence, wherever it sits inside an argument.
_PLACEHOLDER_ANYWHERE_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")

#: Probe alphabet for an int parameter position: every value the int-range
#: grammar can express (its builder tops out at two digits), witnesses at
#: several longer digit-lengths, and the leading-zero forms the grammar
#: forbids. A capture is interrogated by asking which of these it matches, so
#: the alphabet decides what "this capture can render an inadmissible value" is
#: able to see.
_INT_PROBE_VALUES: tuple[str, ...] = tuple(
    [str(v) for v in range(100)]
    + [digit * length for length in range(3, 7) for digit in ("1", "9")]
    + ["00", "01", "007"]
)

#: Witnesses no unit position admits — one per letter case an over-wide capture
#: is written in, since a `[A-Z]+` and a `[a-z]+` capture each need a witness
#: they match to be seen as reaching past the vocabulary.
_NON_UNIT_PROBE_VALUES: tuple[str, ...] = ("NOTAUNIT", "notaunit")

#: Probe alphabet for a unit parameter position: every unit any family allows,
#: so a capture reaching a sibling family's unit is seen, plus the non-unit
#: witnesses above.
_UNIT_PROBE_VALUES: tuple[str, ...] = tuple(
    sorted(
        {
            unit
            for spec in FAMILIES.values()
            for param in spec.get("params") or ()
            if param["kind"] == "unit"
            for unit in param["allowed"]
        }
        | set(_NON_UNIT_PROBE_VALUES)
    )
)


def param_probe_values(param: dict[str, Any]) -> tuple[str, ...]:
    """The probe alphabet for one parameter position.

    Empty for a kind whose admissible values are not a vocabulary this can
    enumerate — a timezone, where the manifest states an open pattern rather
    than a member list, so no finite alphabet interrogates a capture usefully.
    """
    return {
        "int": _INT_PROBE_VALUES,
        "unit": _UNIT_PROBE_VALUES,
    }.get(param["kind"], ())


def _admissible_values(
    param: dict[str, Any],
    params: list[dict[str, Any]],
    literals: dict[str, str] | None = None,
) -> tuple[str, ...] | None:
    """Everything one position admits, or None when that set is not finite.

    None is what separates the two conclusions the probe alphabet supports. A
    probe the capture matches and the position refuses always proves the rule
    wrong. The converse — "matches no probe, so it renders nothing admissible"
    — holds only where the alphabet contains the WHOLE admissible set, which an
    unbounded int position (`FixedSizeBinary` byte_width) does not have.
    """
    kind = param["kind"]
    if kind == "unit":
        return tuple(param["allowed"])
    if kind != "int":
        return None
    lo, hi = resolved_int_bounds(param, params, literals)
    return None if hi is None else tuple(str(v) for v in range(lo, hi + 1))


def _uncovered_admissible_values() -> list[str]:
    """Admissible values of a finite position the probe alphabet misses.

    Non-empty means the completeness `_admissible_values` relies on no longer
    holds, so "matches no probe" would stop implying "renders nothing
    admissible" and `validate_template_bounds` could refuse a sound rule.
    """
    uncovered: list[str] = []
    for name in FAMILY_NAMES:
        params: list[dict[str, Any]] = FAMILIES[name].get("params") or []
        for param in params:
            probes = set(param_probe_values(param))
            if not probes:
                continue
            admissible = _admissible_values(param, params)
            uncovered += [
                f"{name}.{param['name']}={v}"
                for v in admissible or ()
                if v not in probes
            ]
    return uncovered


_probe_gaps = _uncovered_admissible_values()
if _probe_gaps:
    raise RuntimeError(
        f"the probe alphabet no longer covers every value a finite parameter "
        f"position admits: {_probe_gaps[:8]}. The vendored grammar widened a "
        "position beyond the alphabet — widen the probe values to match before "
        "the bound check can be trusted."
    )


def _parse_args(value: str) -> tuple[dict[str, Any], list[str]] | None:
    """`Family(a, b)` -> (family spec, stripped argument list).

    None when the value is not a parameterized family application at all —
    pattern validation owns shape errors, so this never diagnoses them.
    """
    head, sep, rest = value.partition("(")
    spec = FAMILIES.get(head.strip())
    if not sep or spec is None or not spec.get("params"):
        return None
    return spec, [a.strip() for a in rest.rstrip(")").split(",")]


def validate_cross_params(value: str) -> None:
    """Enforce cross-parameter bounds a per-position regex cannot express.

    The manifest states `Decimal128/256` scale as `min 0, max "precision"` — a
    relation between two positions. Both positions must be literal digits for
    the relation to be decidable here; a `${name}` placeholder is skipped
    (it carries no value to compare, and `validate_template_bounds` is what
    reasons about what it can become). Raises ValueError on violation.
    """
    parsed = _parse_args(value)
    if parsed is None:
        return
    spec, args = parsed
    named = {
        param["name"]: args[i]
        for i, param in enumerate(spec["params"])
        if i < len(args)
    }
    for param in spec["params"]:
        for bound, ok, symbol in _CROSS_BOUNDS:
            ref = param.get(bound)
            if not isinstance(ref, str):
                continue
            own, other = named.get(param["name"]), named.get(ref)
            if own is None or other is None or not (own.isdigit() and other.isdigit()):
                continue
            if not ok(int(other), int(own)):
                raise ValueError(
                    f"{value!r}: {param['name']} ({own}) must be "
                    f"{symbol} {ref} ({other})"
                )


# ---------------------------------------------------------------------------
# Recorded wire samples
# ---------------------------------------------------------------------------


def declares_zone(value: str) -> bool | None:
    """Whether canonical `value` declares a zone — None when it cannot say.

    None is "there is nothing here a sample could contradict": the family
    carries no timezone position at all (`Date32`, `Utf8`, `Time32(SECOND)`),
    the value is not a parameterized application, or the position is templated
    and so has no value yet. A True/False answer is returned only for a family
    that HAS the position, which is what keeps a caller from reading "declares
    no zone" out of a type that could never carry one.
    """
    parsed = _parse_args(value)
    if parsed is None:
        return None
    spec, args = parsed
    params = spec["params"]
    index = next(
        (i for i, p in enumerate(params) if p["kind"] == TIMEZONE_PARAM_KIND),
        None,
    )
    if index is None:
        return None
    if index >= len(args):
        # The position is optional and was omitted — a zone-naive declaration.
        return False
    arg = args[index]
    if _PLACEHOLDER_ANYWHERE_RE.search(arg):
        return None
    return arg != params[index]["null_sentinel"]


def _read_wire_datetime(sample: Any) -> re.Match[str] | None:
    """The date-time shape a sample takes, or None if it takes none.

    Shape only — whether the digits name a real instant is
    :func:`sample_names_an_instant`'s question, because the two answers are
    used for different things: one decides whether a sample is evidence about
    zones, the other decides whether it is evidence at all.
    """
    if not isinstance(sample, str):
        return None
    # The recorded string, as recorded. Stripping first read `" …05Z "` as the
    # same wire value as `"…05Z"`, on the reading that a copy carries the
    # whitespace around what was selected — but the authoring rule is that a
    # sample is copied verbatim, so that whitespace is either the provider's,
    # in which case the value is not an RFC 3339 date-time and this profile
    # has no answer about it, or the author's, in which case silence is what a
    # sloppy sample costs and never a wrong verdict.
    #
    # RULE-ENDP-064 settles it: that rule grades the same entry against the
    # node's own schema as the literal string it is. Two rules reading one
    # sample differently is the disagreement the negating-position exemption
    # exists to prevent, and normalising here re-created it one layer down.
    #
    # `fullmatch`, not `match`: the pattern is anchored and `$` matches before
    # a trailing newline, so `match` would read anything appended to a
    # date-time as a date-time. Same trap `_validate_arrow_type_in_json_schema`
    # names, in the direction it applies here.
    return _WIRE_DATETIME_RE.fullmatch(sample)


def _offset_is_real(zone: str | None) -> bool:
    """Whether a captured zone names an offset that exists.

    True for an absent zone and for `Z`, neither of which is an offset. The
    profile admits `+HH`, `+HHMM` and `+HH:MM` — a fixed count of digits in
    each position, and no bound on what they say, so `+25:99` is well-formed
    and names nothing. The positions are read against the same RFC 3339
    productions the clock positions are, through the same constants: the
    grammar spells the hour and the minute once and both readings are of it.
    """
    if zone is None or zone in ("Z", "z"):
        return True
    digits = zone[1:].replace(":", "")
    hour = int(digits[:2])
    minute = int(digits[2:]) if len(digits) > 2 else 0
    return hour <= _MAX_WIRE_HOUR and minute <= _MAX_WIRE_MINUTE


def sample_names_an_instant(sample: Any) -> bool | None:
    """Whether a date-time-shaped sample names a moment that exists.

    The shape regex answers which spellings the authoring profile admits; it
    cannot answer whether `2024-13-45T99:99:99Z` is a date, and reading a
    month of 13 as evidence about anything is worse than reading nothing.

    The calendar arithmetic — which months have 31 days, which years have a
    29th of February — is `datetime.date`'s, from the captured positions.
    What is deliberately NOT delegated is which spellings count: handing the
    whole string to a parser would make this profile whatever that parser
    accepts on the interpreter in front of it, and the profile is the
    contract's to define. `datetime.fromisoformat` is the worked example — the
    spellings admitted here arrived in it only in 3.11, so a reader resting on
    it called every zoned wire value impossible on interpreters these packages
    then supported, in a finding telling the author to drop the evidence.
    Constructing from the positions asks the calendar and nothing else, and
    answers the same everywhere.

    A second of 60 is a leap second, which RFC 3339 admits and providers emit;
    `datetime` has no representation for one, so the seconds position is
    range-checked here rather than constructed. The offset is range-checked for
    the same reason and to the same end: `+25:99` is a `Z` on a moment that
    does not exist, and reading one as evidence of a zone is the whole failure
    this function was written to stop.

    Three answers, like its neighbours: True for a real instant, False for a
    value written as a date-time that names no such moment, and None for one
    this reader cannot verify — a value with no date-time shape at all, and
    the two spellings below that are well-formed and unanswerable. None rather
    than True in every such case: an epoch, a provider spelling or a leap
    second has not been verified, and answering True would let a caller read
    "this is a real instant" out of a value nothing read.
    """
    match = _read_wire_datetime(sample)
    if match is None:
        return None
    if (int(match.group("hour")) > _MAX_WIRE_HOUR
            or int(match.group("minute")) > _MAX_WIRE_MINUTE):
        return False
    # A leap second is real on the wire and unverifiable here. RFC 3339 admits
    # `:60` and providers emit it, but WHICH instants had one is a table the
    # IANA database owns and this repo vendors nothing of — so `:60` is
    # accepted on every date by anything that only range-checks it, which
    # reads a mistyped `23:59:60` on an ordinary day as a real instant and
    # decides a declaration from it. Refusing it instead would tell an author
    # to drop evidence that is genuinely valid. Neither, then: no answer.
    second = match.group("second")
    if second is not None and int(second) == _MAX_WIRE_SECOND:
        return None
    if second is not None and int(second) > _MAX_WIRE_SECOND:
        return False
    # `-00:00` is RFC 3339 §4.3's "the UTC time is known, the local offset is
    # not". Numerically it is `+00:00`; as a statement it is the one offset
    # spelling that declines to name a zone, so reading it as ordinary zone
    # evidence asserts more than the value does — and refusing it would be
    # worse, since the instant it names is perfectly real.
    zone = match.group("zone")
    if (zone is not None and zone[0] == "-"
            and not int(zone[1:].replace(":", "").ljust(4, "0"))):
        return None
    if not _offset_is_real(zone):
        return False
    try:
        date(int(match.group("year")), int(match.group("month")),
             int(match.group("day")))
    except ValueError:
        return False
    return True


def sample_carries_zone(sample: Any) -> bool | None:
    """Whether a recorded wire sample carries a zone — None when it cannot say.

    Only a string in :data:`_WIRE_DATETIME_PATTERN` form naming a real instant
    is decidable. Every other sample returns None, which reads downstream as
    "no evidence" and never as "no zone": a provider that sends `1712345678`
    has not said its instants are naive, it has said nothing this reader can
    hear, and a sample reading `2024-13-45T99:99:99Z` has said nothing either
    — a `Z` on an impossible date is not a report about zones.
    """
    match = _read_wire_datetime(sample)
    # `is not True`, not `is False`: an unverifiable instant — a leap second,
    # an unknown local offset — is exactly as mute about zones as an
    # impossible one. Testing only for the refusal let those read as zone
    # evidence, which is the one answer neither of them carries.
    if match is None or sample_names_an_instant(sample) is not True:
        return None
    return match.group("zone") is not None


def _describe_bounds(lo: int, hi: int | None) -> str:
    return f"{lo}-{hi}" if hi is not None else f"{lo} or above"


def _describe_position(
    param: dict[str, Any],
    params: list[dict[str, Any]],
    literals: dict[str, str],
) -> str:
    """What one position admits, phrased for a diagnostic."""
    if param["kind"] == "int":
        return _describe_bounds(*resolved_int_bounds(param, params, literals))
    return " or ".join(_admissible_values(param, params, literals) or ())


def validate_template_bounds(
    value: str,
    capture_language: Callable[[str, tuple[str, ...]], frozenset[str] | None],
) -> None:
    """Reject a templated canonical whose captures can render a non-canonical.

    A templated position carries no value of its own, so what it renders comes
    from the thing that produces it: the native matcher's named capture.
    `capture_language(name, probes)` answers which of `probes` that capture can
    match (None when it cannot be read), and three things follow:

    - a **templated** position must not be able to render a value its own
      position does not admit — a `(?<n>\\d+)` feeding `FixedSizeBinary(${n})`
      can render a byte width of 0, and where a cross-parameter bound applies
      "admit" resolves it against the literal sibling actually present, so
      `Decimal128(5, ${s})` admits 0-5;
    - a templated position whose capture matches NOTHING the position admits
      renders only inadmissible values, wherever the admissible set is finite
      enough for the probe alphabet to hold all of it (`_admissible_values`);
    - a **literal** position whose bound names a TEMPLATED sibling must hold
      against every value that sibling can render — `Decimal128(${p}, 38)` is
      satisfiable only at `p == 38`, so a capture reaching any smaller
      precision is refused.

    Deliberately left wide:

    - where a cross-parameter bound carries a placeholder on each side, every
      capture is judged against its own position, so `Decimal128(${p}, ${s})`
      passes even though `(1, 38)` is a reachable pair. Deciding it needs the
      joint language of the captures over one native string, which this does
      not compute;
    - a position whose kind has no probe alphabet (a timezone, stated as an
      open pattern) is not interrogated at all.

    Raises ValueError on violation.
    """
    parsed = _parse_args(value)
    if parsed is None:
        return
    spec, args = parsed
    params: list[dict[str, Any]] = spec["params"]
    if len(args) > len(params):
        return
    by_name = {param["name"]: param for param in params}
    placeholders: dict[str, str] = {}
    literals: dict[str, str] = {}
    for param, arg in zip(params, args):
        match = _PLACEHOLDER_ARG_RE.fullmatch(arg)
        if match:
            placeholders[param["name"]] = match.group(1)
        elif _PLACEHOLDER_ANYWHERE_RE.search(arg):
            # Neither a value to compare nor a capture to interrogate: what a
            # concatenation renders is the product of its parts, which no
            # single capture's language answers. Refused rather than filed
            # under literals, where the digit guard would wave it through.
            raise ValueError(
                f"{value!r}: the {param['name']} position is {arg!r}; a "
                "parameter position carrying a ${name} placeholder must be "
                "that placeholder alone, so what it renders can be decided "
                "from the capture feeding it"
            )
        else:
            literals[param["name"]] = arg

    def _produced(param: dict[str, Any], admissible_only: bool) -> list[str] | None:
        """What the capture bound to `param` can render, in probe-alphabet order
        (so a diagnostic leads with the plainest witness), optionally narrowed
        to the values that position admits on its own.

        None when nothing can be concluded — an unreadable capture, or a
        position whose kind carries no probe alphabet to interrogate it with
        (an empty answer there would read as "matches nothing", which is the
        opposite of what an empty alphabet means)."""
        probes = param_probe_values(param)
        if not probes:
            return None
        rendered = capture_language(placeholders[param["name"]], probes)
        if rendered is None:
            return None
        ordered = [v for v in probes if v in rendered]
        if not admissible_only:
            return ordered
        allowed = re.compile(_param_literal_pattern(param, params, literals))
        return [v for v in ordered if allowed.fullmatch(v)]

    for param in params:
        name = param["name"]
        if name not in placeholders and name not in literals:
            continue  # an optional trailing position the canonical omits
        if name in placeholders:
            rendered = _produced(param, admissible_only=False)
            if rendered is None:
                continue
            allowed = re.compile(_param_literal_pattern(param, params, literals))
            refused = [v for v in rendered if not allowed.fullmatch(v)]
            admits = _describe_position(param, params, literals)
            if refused:
                raise ValueError(
                    f"{value!r}: the ${{{placeholders[name]}}} capture can match "
                    f"{refused[:4]}, which the {name} position does not admit "
                    f"({admits}); narrow the native's "
                    f"(?<{placeholders[name]}>…) capture to that range"
                )
            if not rendered and _admissible_values(param, params, literals):
                # The alphabet holds every value a finite position admits, so
                # an empty language means the capture can only ever render one
                # the position refuses — whatever width it is. This is what
                # carries a capture matching only widths past the last witness.
                raise ValueError(
                    f"{value!r}: the ${{{placeholders[name]}}} capture matches no "
                    f"value the {name} position admits ({admits}); every native "
                    f"it matches would render a canonical the contract refuses"
                )
            continue
        own = literals[name]
        for bound, ok, symbol in _CROSS_BOUNDS:
            ref = param.get(bound)
            if not isinstance(ref, str) or ref not in placeholders or not own.isdigit():
                continue
            # A ref naming no sibling is already fatal: the templated position
            # this bound points at resolves through `resolved_int_bounds`,
            # which refuses a dangling name self-describingly.
            rendered = _produced(by_name[ref], admissible_only=True)
            if rendered is None:
                continue
            refused = [
                v for v in rendered if v.isdigit() and not ok(int(v), int(own))
            ]
            if refused:
                raise ValueError(
                    f"{value!r}: {name} is the literal {own}, but the "
                    f"${{{placeholders[ref]}}} capture can match {refused[:4]}, "
                    f"which would render {name} {symbol} {ref} false; bound the "
                    f"native's (?<{placeholders[ref]}>…) capture or template "
                    f"{name} too"
                )
