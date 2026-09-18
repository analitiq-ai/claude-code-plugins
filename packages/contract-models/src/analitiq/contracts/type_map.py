"""Type-map contract models — the on-disk `type-map-read.json` /
`type-map-write.json` files a connector ships under its `definition/`.

Each file is a top-level JSON object `{$schema, direction, rules}`: `direction`
is the fixed literal naming which file this is (`"read"` / `"write"`), and
`rules` is an ordered, non-empty array of `{match, native_type, arrow_type}`
rules (first match wins). The two directions share the rule shape but invert
which key is the *matcher* and which is *rendered*:

- **read**  (`native_type → arrow_type`): match on `native_type`, render
  `arrow_type` (the rendered side is the Apache Arrow vocabulary).
- **write** (`arrow_type → native_type`): match on `arrow_type`, render
  `native_type` (the rendered side is free-form dialect DDL).

Source of truth for both the published `type-map-read` / `type-map-write` JSON
Schemas and the connector validator (which validates via `model_validate`).
Only *error*-level rules live here — things that make a document invalid.
Advisory quality checks that the contract tolerates (duplicate rules, dead
uppercase-only patterns, write-vocabulary coverage gaps) are not contract
violations and stay in the validator as warnings.
"""
from __future__ import annotations

import functools
import itertools
import re
import sys
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import re2
from pydantic import Field, model_validator

from analitiq.contracts.arrow_grammar import (
    CONTAINER_CANONICAL_HEADS as _CONTAINER_CANONICAL_HEADS,
    TEMPLATE_DUMMY_SUBSTITUTIONS,
    validate_cross_params,
    validate_template_bounds,
)
from analitiq.contracts.endpoints import ARROW_TYPE_PATTERN
from analitiq.contracts.shared.common import StrictModel, schema_url_for
from analitiq.contracts.shared.rules import violation

#: Per-direction schema URLs declared by every `type-map-read.json` /
#: `type-map-write.json` document — always a standalone file (never an
#: embedded API payload), so the field is required, unlike the optional
#: `$schema` on dual-use models such as `ConnectionAuthored`.
TYPE_MAP_READ_SCHEMA_URL = schema_url_for("type-map-read")
TYPE_MAP_WRITE_SCHEMA_URL = schema_url_for("type-map-write")

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

# A refused matcher is reported as a finding; RE2 would also write it to stderr.
_RE2_OPTIONS = re2.Options()
_RE2_OPTIONS.log_errors = False

_FOLD_CASE_FLAG = "i"
_OCTAL_DIGITS = "01234567"
# What an escape letter means is RE2's grammar; this tokenizer restates the part
# of it the compiled program does not expose, and refuses a letter it does not
# know. The escapes spelling one character are read by `_escaped_character`.
_CONTROL_ESCAPES = {"a": "\a", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
_CHARACTER_SET_ESCAPES = frozenset("dDsSwWCpP")
_ZERO_WIDTH_ESCAPES = frozenset("bBAz")
# RE2 reads a repetition count in ASCII digits only; `{` followed by anything
# else is a literal.
_REPETITION = re.compile(r"\{[0-9]+(?:,[0-9]*)?\}")


@dataclass(frozen=True, slots=True)
class _Atom:
    """One position of a matcher that consumes exactly one character.

    `text` is the atom as the matcher spells it. `pattern` is a pattern of its
    own matching the characters the atom matches where it sits, less the inline
    `flags` in force there. `literal` is the character itself when the atom
    stands for exactly one."""

    text: str
    pattern: str
    literal: str | None
    flags: frozenset[str]


@dataclass(frozen=True, slots=True)
class _GroupOpen:
    """A group opener; `flags` are the inline flags in force inside it, and
    `capture` is the group's number in RE2's numbering, None when it does not
    capture."""

    opener: str
    capture: int | None
    end: int
    flags: frozenset[str]


@dataclass(frozen=True, slots=True)
class _GroupClose:
    opened_by: _GroupOpen
    start: int


_Token = _Atom | _GroupOpen | _GroupClose


@dataclass(frozen=True, slots=True)
class CompiledMatcher:
    """A type-map matcher compiled in RE2, with the tokens it was read into."""

    # The binding publishes no type for a compiled pattern.
    regex: Any
    tokens: tuple[_Token, ...]

    @property
    def literal_text(self) -> str:
        """The matcher's literal characters in order, with its syntax dropped."""
        return "".join(
            token.literal for token in self.tokens
            if isinstance(token, _Atom) and token.literal is not None
        )


def _re2_error_text(exc: Exception) -> str:
    detail = exc.args[0] if exc.args else exc
    return detail.decode("utf-8", "replace") if isinstance(detail, bytes) else str(detail)


def _re2_compile(pattern: str) -> Any:
    """ValueError carrying RE2's own parse error when RE2 refuses `pattern`."""
    try:
        return re2.compile(pattern, options=_RE2_OPTIONS)
    except re2.error as exc:
        raise ValueError(f"matcher is not valid RE2 ({_re2_error_text(exc)})") from exc


def compile_matcher(pattern: str) -> CompiledMatcher:
    """Compile a type-map matcher in RE2, the dialect the rule is matched in.

    ValueError carrying RE2's own parse error when RE2 refuses the pattern, and
    when a named group is spelled `(?P<name>…)`: RE2 takes that spelling too, so
    that refusal is the contract's choice of one spelling, not the dialect's.
    """
    regex = _re2_compile(pattern)
    try:
        tokens = _tokenize(pattern)
    except (ValueError, IndexError) as exc:
        # RE2 accepted the pattern, so this is the tokenizer's defect and never
        # the author's: it must not reach the caller as a refused matcher.
        raise RuntimeError(
            f"the matcher tokenizer cannot read {pattern!r}, which RE2 accepted"
        ) from exc
    if any(isinstance(t, _GroupOpen) and t.opener.startswith("(?P<") for t in tokens):
        raise ValueError(
            "matcher spells a named group '(?P<name>…)'; the contract takes "
            "only '(?<name>…)'"
        )
    return CompiledMatcher(regex, tokens)


def _with_flags(pattern: str, flags: frozenset[str]) -> str:
    return f"(?{''.join(sorted(flags))}:{pattern})" if flags else pattern


def _compile_fragment(fragment: str, source: str) -> Any:
    """Compile a fragment the tokenizer cut out of the compiled matcher `source`.

    Every fragment of a pattern RE2 accepted is itself a pattern RE2 accepts, so
    a refusal here means the tokenizer cut `source` where RE2 does not, and no
    verdict read from the fragment could be trusted."""
    try:
        return compile_matcher(fragment).regex
    except ValueError as exc:
        raise RuntimeError(
            f"{fragment!r}, read out of the matcher {source!r}, does not compile "
            "on its own: the matcher tokenizer does not split this pattern the "
            "way RE2 parses it"
        ) from exc


def _literal(char: str, flags: frozenset[str], text: str | None = None) -> _Atom:
    return _Atom(text or char, f"\\x{{{ord(char):X}}}", char, flags)


def _escaped_character(escape: str) -> str | None:
    """The one character `escape` spells, or None when it spells a set of
    characters or a position."""
    letter = escape[1]
    if letter.isascii() and not letter.isalnum():
        return letter
    if letter == "x":
        return chr(int(escape[2:].strip("{}"), 16))
    if letter in _OCTAL_DIGITS:
        return chr(int(escape[1:], 8))
    return _CONTROL_ESCAPES.get(letter)


def _escape_end(pattern: str, start: int) -> int:
    """Where the escape opening at `pattern[start]` (a backslash) ends."""
    letter = pattern[start + 1]
    if letter in "pPx" and pattern.startswith("{", start + 2):
        return pattern.index("}", start + 3) + 1
    if letter in "pP":
        return start + 3
    if letter == "x":
        return start + 4
    if letter in _OCTAL_DIGITS:
        end = start + 2
        while end < min(start + 4, len(pattern)) and pattern[end] in _OCTAL_DIGITS:
            end += 1
        return end
    return start + 2


def _class_end(pattern: str, start: int) -> int:
    """Where the character class opening at `pattern[start]` (a `[`) ends: after
    the first `]` closing a class RE2 compiles on its own.

    Each `]` before the class's own end is a member, so the class cut there is
    unterminated and RE2 refuses it. Asking RE2 leaves its class grammar — a
    `]` first in the class, POSIX classes, a range ending at `[` — to RE2."""
    end = start + 1
    while True:
        end = pattern.index("]", end) + 1
        try:
            _re2_compile(pattern[start:end])
        except ValueError:
            continue
        return end


def _tokenize(pattern: str) -> tuple[_Token, ...]:
    """Split a pattern RE2 has compiled into its one-character atoms and groups.

    Only called from `compile_matcher`, after RE2 accepted the pattern, so every
    construct is well formed. Anchors, alternation and repetition are consumed
    without a token. An inline flag group applies to the rest of the group it
    sits in, as RE2 scopes it."""
    tokens: list[_Token] = []
    flags: frozenset[str] = frozenset()
    open_groups: list[tuple[_GroupOpen, frozenset[str]]] = []
    captures = itertools.count(1)
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if pattern.startswith("\\Q", i):
            quote_end = pattern.find("\\E", i + 2)
            if quote_end < 0:
                quote_end = len(pattern)
            tokens.extend(_literal(c, flags) for c in pattern[i + 2:quote_end])
            i = quote_end + 2
        elif char == "\\":
            end = _escape_end(pattern, i)
            letter = pattern[i + 1]
            if (character := _escaped_character(pattern[i:end])) is not None:
                tokens.append(_literal(character, flags, pattern[i:end]))
            elif letter in _CHARACTER_SET_ESCAPES:
                tokens.append(_Atom(pattern[i:end], pattern[i:end], None, flags))
            elif letter not in _ZERO_WIDTH_ESCAPES:
                raise RuntimeError(
                    f"RE2 accepted the escape {pattern[i:end]!r} in {pattern!r}, "
                    "which the matcher tokenizer does not know"
                )
            i = end
        elif char == "[":
            end = _class_end(pattern, i)
            tokens.append(_Atom(pattern[i:end], pattern[i:end], None, flags))
            i = end
        elif char == "(":
            inner = flags
            capture = None
            if pattern.startswith(("(?<", "(?P<"), i):
                opener = pattern[i:pattern.index(">", i) + 1]
                capture = next(captures)
            elif pattern.startswith("(?", i):
                spec_end = min(k for k in (pattern.find(")", i), pattern.find(":", i)) if k >= 0)
                on, _, off = pattern[i + 2:spec_end].partition("-")
                inner = (flags | set(on)) - set(off)
                if pattern[spec_end] == ")":
                    flags = inner
                    i = spec_end + 1
                    continue
                opener = pattern[i:spec_end + 1]
            else:
                opener = "("
                capture = next(captures)
            group = _GroupOpen(opener, capture, i + len(opener), inner)
            tokens.append(group)
            open_groups.append((group, flags))
            flags = inner
            i = group.end
        elif char == ")":
            group, flags = open_groups.pop()
            tokens.append(_GroupClose(group, i))
            i += 1
        elif char == "{" and (repetition := _REPETITION.match(pattern, i)):
            i = repetition.end()
        elif char in "*+?^$|":
            i += 1
        elif char == ".":
            tokens.append(_Atom(char, char, None, flags))
            i += 1
        else:
            tokens.append(_literal(char, flags))
            i += 1
    return tuple(tokens)


def _named_group_source(pattern: str, name: str) -> str:
    """The `(?<name>…)` group's sub-pattern, as a pattern of its own: the source
    between its opener and closer, under the inline flags in force there. A
    name given to more than one group is read as the group RE2 binds it to.

    KeyError when the matcher has no group of that name."""
    compiled = compile_matcher(pattern)
    capture = compiled.regex.groupindex[name]
    for token in compiled.tokens:
        if isinstance(token, _GroupClose) and token.opened_by.capture == capture:
            group = token.opened_by
            return _with_flags(pattern[group.end:token.start], group.flags)
    raise RuntimeError(
        f"RE2 numbers the {name!r} group of {pattern!r} {capture}, and the matcher "
        "tokenizer closes no group it numbers so"
    )


def _capture_language(native: str, name: str, probes: tuple[str, ...]) -> frozenset[str]:
    """Which of `probes` the native's `(?<name>…)` capture can match.

    An over-approximation on purpose: the capture is interrogated in isolation,
    so surrounding context that would further constrain it is ignored."""
    capture = _compile_fragment(_named_group_source(native, name), native)
    return frozenset(probe for probe in probes if capture.fullmatch(probe))


def _kept_by_normalization(chars: str) -> str:
    """The members of `chars` that `normalize_native_type` leaves as they are.

    Each sits between two `A`s, which normalization keeps, so stripping never
    reaches one and a whitespace member is a run of its own. A block that comes
    back unchanged is kept whole, and only a changed one is split."""
    probe = "A" + "A".join(chars) + "A"
    if normalize_native_type(probe) == probe:
        return chars
    if len(chars) == 1:
        return ""
    half = len(chars) // 2
    return _kept_by_normalization(chars[:half]) + _kept_by_normalization(chars[half:])


@functools.cache
def _normalized_native_characters() -> bytes:
    """Every character a normalized native can contain, concatenated, as UTF-8.

    Read off `normalize_native_type` itself rather than a model of it. Bytes
    because the RE2 binding re-encodes a `str` subject on every search, and this
    subject is searched for every atom. Surrogates are excluded: they are not
    characters and have no UTF-8 encoding."""
    every = "".join(map(chr, itertools.chain(range(0xD800), range(0xE000, sys.maxunicode + 1))))
    block = 1024
    return "".join(
        _kept_by_normalization(every[i:i + block]) for i in range(0, len(every), block)
    ).encode()


def case_dead_atoms(matcher: str) -> tuple[str, ...]:
    """The atoms of a read matcher that no normalized native can satisfy only
    because of their case, as authored.

    An atom is case-dead when, under the inline flags in force where it sits, it
    matches no character a normalized native contains, while its
    case-insensitive form matches one. ValueError when the matcher does not
    compile."""
    characters = _normalized_native_characters()

    def matches_one(pattern: str) -> bool:
        return _compile_fragment(pattern, matcher).search(characters) is not None

    return tuple(
        token.text for token in compile_matcher(matcher).tokens
        if isinstance(token, _Atom)
        and not matches_one(_with_flags(token.pattern, token.flags))
        and matches_one(_with_flags(token.pattern, token.flags | {_FOLD_CASE_FLAG}))
    )


def _native_is_schemaless_container(literal_text: str) -> bool:
    """Best-effort, DB-agnostic detection of a structured/container native from
    its SYNTAX — never a vendor type-name list. Container natives are recognised
    by shape: angle-bracket parameterization (`array<int>`, `struct<...>`,
    `map<k, v>`) or a SQL array suffix (`integer[]`), read from the native's
    literal characters, so a regex rule's own syntax is never mistaken for
    either. Bare vendor scalars-for-JSON (`JSONB`, `VARIANT`, …) are
    intentionally not special-cased; if their structure matters the author
    writes it with `<...>` or `[]`."""
    return ("<" in literal_text and ">" in literal_text) or literal_text.endswith("[]")


def _guard_container_not_collapsed(native_type: str, literal_text: str, arrow_type: str) -> None:
    """A schemaless/structured native_type must not resolve to a scalar Arrow type
    (which would silently drop the value's structure). Read direction only.
    `literal_text` is what container syntax is read from: the native itself for
    an exact rule, the matcher's literal characters for a regex one."""
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
        try:
            _guard_container_not_collapsed(self.native_type, compiled.literal_text, self.arrow_type)
        except ValueError as detail:
            raise violation("RULE-TMAP-002", "read-regex-container-collapsed", str(detail)) from None

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
# runtime-only render/capture checks. Both directions render a `oneOf` with a
# `match` discriminator, so external validators reject exactly what the model does.
TypeMapReadRule = Annotated[
    TypeMapReadExactRule | TypeMapReadRegexRule,
    Field(discriminator="match"),
]
TypeMapWriteRule = Annotated[
    TypeMapWriteExactRule | TypeMapWriteRegexRule,
    Field(discriminator="match"),
]


class TypeMapReadDoc(StrictModel):
    """`type-map-read.json`: the read direction's `$schema` + `direction` +
    ordered, non-empty `rules` array."""

    schema_url: Literal[TYPE_MAP_READ_SCHEMA_URL] = Field(
        ...,
        alias="$schema",
        description="Schema URL declared by every `type-map-read.json` document.",
    )
    direction: Literal["read"] = Field(
        ..., description="Fixed direction discriminator for this file."
    )
    rules: Annotated[list[TypeMapReadRule], Field(min_length=1)]


class TypeMapWriteDoc(StrictModel):
    """`type-map-write.json`: the write direction's `$schema` + `direction` +
    ordered, non-empty `rules` array."""

    schema_url: Literal[TYPE_MAP_WRITE_SCHEMA_URL] = Field(
        ...,
        alias="$schema",
        description="Schema URL declared by every `type-map-write.json` document.",
    )
    direction: Literal["write"] = Field(
        ..., description="Fixed direction discriminator for this file."
    )
    rules: Annotated[list[TypeMapWriteRule], Field(min_length=1)]
