"""Engine-published Arrow type grammar — the vendored capability manifest.

The set of canonical Arrow type families the platform executes end-to-end is a
**capability surface**: only the engine can make a statement about it true, so
the engine owns it (issue #81). analitiq-core publishes the vocabulary as a
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
(engine#413; grammar v1.1.0 and matrix v2.0.0 on). The pin below still states
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
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
#: Key each artifact stamps its own version under (engine#413). Both artifacts
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


def _param_literal_pattern(param: dict[str, Any], params: list[dict[str, Any]]) -> str:
    """Regex for one parameter position's LITERAL values, from its spec.

    `params` is the owning family's full param list, needed to resolve a
    cross-parameter bound (`"max": "precision"`): the relation itself cannot
    live in a per-position regex (`validate_cross_params` enforces it on
    literals), but the referenced param's own numeric ceiling can — scale <=
    precision <= 38 means a literal scale above 38 is unsatisfiable for ANY
    precision, so the pattern rejects it outright. This also closes the
    templated hole: `Decimal128(${p}, 99)` can never be satisfied and now
    fails both the published template pattern and the runtime dummy check.
    """
    kind = param["kind"]
    if kind == "int":
        lo = param["min"]
        hi = param["max"]
        if isinstance(hi, str):
            ref = next((p for p in params if p["name"] == hi), None)
            if ref is None:
                # A dangling ref would otherwise silently disable the bound at
                # BOTH layers: unbounded pattern here, and a silent skip in
                # `validate_cross_params` (named.get(ref) is None).
                raise ValueError(
                    f"param {param['name']!r} bound references unknown "
                    f"sibling param {hi!r}"
                )
            hi = ref["max"] if isinstance(ref.get("max"), int) else None
        return _int_range_pattern(lo, hi)
    if kind == "unit":
        return "(?:" + "|".join(param["allowed"]) + ")"
    if kind == "timezone":
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


def validate_cross_params(value: str) -> None:
    """Enforce cross-parameter bounds a per-position regex cannot express.

    The manifest states `Decimal128/256` scale as `min 0, max "precision"` — a
    relation between two positions. For a LITERAL canonical (both positions
    digits) the relation is checked here; templated canonicals skip it (a
    placeholder has no value to compare). Raises ValueError on violation.
    Values that don't parse as `Family(args)` are ignored — pattern validation
    owns shape errors.
    """
    head, sep, rest = value.partition("(")
    spec = FAMILIES.get(head.strip())
    if not sep or spec is None or not spec.get("params"):
        return
    args = [a.strip() for a in rest.rstrip(")").split(",")]
    named = {
        param["name"]: args[i]
        for i, param in enumerate(spec["params"])
        if i < len(args)
    }
    for param in spec["params"]:
        for bound, ok in (("min", int.__le__), ("max", int.__ge__)):
            ref = param.get(bound)
            if not isinstance(ref, str):
                continue
            own, other = named.get(param["name"]), named.get(ref)
            if own is None or other is None or not (own.isdigit() and other.isdigit()):
                continue
            if not ok(int(other), int(own)):
                raise ValueError(
                    f"{value!r}: {param['name']} ({own}) must be "
                    f"{'>=' if bound == 'min' else '<='} {ref} ({other})"
                )
