"""Every fenced json/jsonc snippet in this plugin's prose must uphold
the disposition its annotation declares.

`test_examples.py` pins the bundled `examples/*.example.json`, but creator
agents copy shapes from the fenced ``jsonc`` blocks inline in the plugin's
`**/*.md` — skills and agent definitions alike — and those are fragments (a
`mapping` block, a `schedule` object, one assignment), so no complete-document
gate ever sees them. This suite closes that hole over the WHOLE plugin tree,
and it is the extraction gate the annotation convention promised: every inline
fence carries an HTML comment directly above it declaring its verification
contract (`.claude/rules/plugin-prose.md` § "The annotation convention", the
normative home for both plugins). The gate classifies each block FROM that
marker — there is no hand-maintained registry to drift from the prose. What
follows is how THIS gate grades each marker:

* ``<!-- validate: <entity> -->`` — graded twice: the bare fragment must
  validate standalone as a complete ``<entity>`` document (a fragment decayed
  to a single key must not ride a host's completeness), and its top-level
  keys merged into a bundled host example already pinned valid by the sibling
  suite must validate too (catching interplay with the host fields the
  fragment omits).
* ``<!-- validate: <entity>#/<json-pointer> -->`` — the fragment replaces the
  host's value at that pointer. A fragment may show its enclosing key for
  context; the pointer names the deepest shown node and the gate unwraps it.
* ``<!-- invalid: <RULE id> -->`` — deliberately wrong; the spliced document
  must FAIL validation (a "don't do this" example that rots into valid is the
  most misleading rot there is).
* ``<!-- illustrative -->`` — outside the published contract's validation
  surface; exempt from splicing, but must still parse as JSON(C).

Because the host is known-valid, a post-splice failure indicts the fragment:
the prose is teaching a shape the contract rejects. A discovered block with no
parseable marker fails loudly — markers travel with their blocks, so inserting,
reordering, or swapping blocks can never silently re-point a disposition.

Placeholder rule (see `_graft`): a fragment value that is an EMPTY OBJECT
means *elided here* and resolves to the host's value. Deliberate asymmetry:
``[]`` is NOT a placeholder — an empty array is content the fragment states
(e.g. an emptied ``conflict_keys`` must be graded, not papered over). Every
placeholder position is pinned in ``EXPECTED_PLACEHOLDERS`` so emptying a real
fragment cannot silently degrade a splice to grading the host against itself.

Skips cleanly when the published packages are absent, like the other suites.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import pytest

ROOT = Path(__file__).resolve().parents[2] / "plugins" / "analitiq-pipeline-builder"
SKILLS = ROOT / "skills"
sys.path.insert(0, str(ROOT / "scripts"))
import validate as V  # noqa: E402

pytest.importorskip("analitiq.validator",
                    reason="requires: pip install -r requirements-dev.txt")

import test_examples  # noqa: E402  (sibling suite; pytest puts this dir on sys.path)


# ---------------------------------------------------------------------------
# Discovery: every ```json / ```jsonc fence under the plugin's **/*.md
# ---------------------------------------------------------------------------

# A fence opener: a run of >=3 backticks or tildes, then the info string.
# Matched against the line AFTER lstrip, so indented fences (idiomatic inside
# list steps) are seen. Mirrors `_code_fence_spans` in
# scripts/render_validator_claims.py, plus length-aware pairing and info-string
# capture, which this gate needs and that scanner does not.
_FENCE_OPEN = re.compile(r"^(`{3,}|~{3,})\s*(.*?)\s*$")

# The corroboration backstop: any line that LOOKS like a json/jsonc fence
# opener. Every match must be accounted for — as a discovered opener or as
# content inside some tracked fence — or discovery has an escape hatch.
# IGNORECASE is backstop-only: discovery's exact-form match stays lowercase,
# so a case variant (```JSON) is loudly flagged, never silently adopted.
_JSONISH = re.compile(r"^\s*(`{3,}|~{3,})\s*jsonc?\b", re.IGNORECASE)


class Fence(NamedTuple):
    open_at: int   # line index of the opening fence
    close_at: int  # line index of the closing fence
    char: str      # "`" or "~"
    length: int    # opener run length (closer must be >= this)
    info: str      # info string after the opener run, stripped


class Block(NamedTuple):
    marker: str    # raw line directly above the opening fence ("" at file top)
    body: str      # fence contents


def _fence_spans(lines: list[str], where: str) -> list[Fence]:
    """Pair fences line-wise, tolerating indentation, ``` and ~~~ alike.

    Pairing is CommonMark-shaped: a fence opens on a run of >=3 backticks or
    tildes (its info string is whatever follows the run), and closes only on a
    line whose stripped form is a run of the SAME character, at least as LONG
    as the opener, with nothing after it. Everything between is content — so a
    ```jsonc line quoted inside a bash fence is body text, never an opener or
    a closer, and a 4-backtick fence cannot be closed early by a 3-backtick
    line (which would swallow the block that follows). An unterminated fence
    would silently discard its body — and with it a block this gate exists to
    check — so it fails loud instead.
    """
    fences: list[Fence] = []
    open_at: int | None = None
    char, length, info = "", 0, ""
    for i, line in enumerate(lines):
        stripped = line.strip()
        if open_at is None:
            m = _FENCE_OPEN.match(stripped)
            if m:
                run = m.group(1)
                open_at, char, length, info = i, run[0], len(run), m.group(2)
            continue
        run_len = len(stripped) - len(stripped.lstrip(char))
        if run_len >= length and not stripped[run_len:]:
            fences.append(Fence(open_at, i, char, length, info))
            open_at = None
    assert open_at is None, (
        f"{where}: unterminated fence opened at line {open_at + 1}")
    return fences


def _discover_blocks(root: Path) -> tuple[dict[tuple[str, int], Block], list[str]]:
    """-> ({(path relative to root, json[c]-block index): Block}, unaccounted).

    A block is a BACKTICK fence whose info string is exactly ``json`` or
    ``jsonc``; the index counts those alone, per file. Every other fence is
    tracked but opaque, so its body can never be mistaken for top-level
    markdown. ``unaccounted`` lists every `_JSONISH` line that is neither a
    discovered opener nor content inside a tracked fence — tilde, case, and
    info-string variants (``~~~jsonc``, ```` ```JSON ````, ```` ```json
    title="x" ````) land here, converting each would-be escape hatch into a
    loud failure.
    """
    blocks: dict[tuple[str, int], Block] = {}
    unaccounted: list[str] = []
    for md in sorted(root.rglob("*.md")):
        rel = md.relative_to(root).as_posix()
        # Generated, not authored: release-please writes CHANGELOG.md from
        # commit bodies, so a fenced block there is nobody's teaching example
        # and cannot carry an annotation.
        if rel == "CHANGELOG.md":
            continue
        lines = md.read_text().splitlines()
        accounted: set[int] = set()
        index = 0
        for fence in _fence_spans(lines, rel):
            accounted.update(range(fence.open_at + 1, fence.close_at))
            if fence.char == "`" and fence.info in ("json", "jsonc"):
                marker = lines[fence.open_at - 1] if fence.open_at else ""
                body = "\n".join(lines[fence.open_at + 1:fence.close_at])
                blocks[(rel, index)] = Block(marker, body)
                accounted.add(fence.open_at)
                index += 1
        unaccounted.extend(
            f"{rel}:{i + 1}: {line.strip()}"
            for i, line in enumerate(lines)
            if _JSONISH.match(line) and i not in accounted)
    return blocks, unaccounted


DISCOVERED, UNACCOUNTED = _discover_blocks(ROOT)


def test_discovery_scanner(tmp_path):
    """Guard the scanner: exact-form collection, indentation-tolerant, opaque
    elsewhere, length-aware pairing, loud on an unterminated fence."""
    (tmp_path / "probe.md").write_text(
        "```jsonc\n{}\n```\n"                # collected: index 0
        "```bash\necho hi\n```\n"            # other language: opaque
        "1. step\n\n   ```jsonc\n   {1}\n   ```\n"  # indented: collected, idx 1
        "```bash\ncat <<EOF\n```jsonc\nquoted\nEOF\n```\n"
        # ^ bash fence quoting a ```jsonc line: content, no bogus block —
        #   the quoted line has an info string, so it cannot close the fence
        "````\n```json\nquoted too\n````\n"  # 4-backtick fence: ```json is content
        "```json\n[1]\n```\n"                # collected: index 2
    )
    blocks, unaccounted = _discover_blocks(tmp_path)
    assert blocks == {
        ("probe.md", 0): Block("", "{}"),
        ("probe.md", 1): Block("", "   {1}"),
        ("probe.md", 2): Block("````", "[1]"),
    }
    assert unaccounted == []  # both quoted openers sit inside tracked fences

    (tmp_path / "probe.md").write_text("```jsonc\n{}\n")  # never closed
    with pytest.raises(AssertionError, match="unterminated"):
        _discover_blocks(tmp_path)


def test_discovery_backstop_flags_jsonish_lookalikes(tmp_path):
    """A fence that smells like json but evades exact-form discovery must not
    fail open: the backstop names it."""
    (tmp_path / "probe.md").write_text(
        '```json title="x"\n{}\n```\n'  # info-string variant: not collected
        "~~~jsonc\n{}\n~~~\n"           # tilde variant: not collected
        "```JSON\n{}\n```\n"            # case variant: not collected
    )
    blocks, unaccounted = _discover_blocks(tmp_path)
    assert blocks == {}
    assert [u.split(": ", 1)[1] for u in unaccounted] == [
        '```json title="x"', "~~~jsonc", "```JSON"]


def test_real_tree_has_no_unaccounted_jsonish_lines():
    assert not UNACCOUNTED, (
        f"lines that look like json/jsonc fence openers but were not "
        f"discovered: {UNACCOUNTED}. Use an exact ```json / ```jsonc fence "
        "(no info-string extras, no tildes) so the gate collects it, or quote "
        "it inside another fence if it is illustration of markdown itself.")


# ---------------------------------------------------------------------------
# Markers: the annotation convention, parsed
# ---------------------------------------------------------------------------

_MARKER = re.compile(
    r"^\s*<!--\s*(?:"
    r"(?P<illustrative>illustrative)"
    r"|validate:\s*(?P<entity>[a-z_]+)(?:#(?P<pointer>/\S+))?"
    r"|invalid:\s*(?P<rule>RULE-[A-Z]+-\d+)"
    r")\s*-->\s*$")

_CONVENTION = (
    "the annotation convention (.claude/rules/plugin-prose.md § 'The "
    "annotation convention')")


class Marker(NamedTuple):
    kind: str            # "validate" | "invalid" | "illustrative"
    entity: str | None   # validate: adapter entity the spliced doc grades as
    pointer: str | None  # validate: JSON pointer ("/a/b"); None = top-level merge
    rule: str | None     # invalid: the rule the block deliberately breaks

    @property
    def target(self) -> str:
        """The marker's own spelling of what it grades — stable across
        reindexing, so pins key on it rather than on a block index."""
        return f"{self.entity}#{self.pointer}" if self.pointer else str(self.entity)


def _parse_marker(raw: str) -> Marker | None:
    m = _MARKER.match(raw)
    if not m:
        return None
    if m.group("illustrative"):
        return Marker("illustrative", None, None, None)
    if m.group("rule"):
        return Marker("invalid", None, None, m.group("rule"))
    return Marker("validate", m.group("entity"), m.group("pointer"), None)


MARKERS: dict[tuple[str, int], Marker | None] = {
    key: _parse_marker(block.marker) for key, block in DISCOVERED.items()}


def test_every_block_is_annotated():
    """The anti-vacuity property: a block's disposition is declared beside it.

    The marker travels with its block, so inserting, reordering, or swapping
    same-count blocks cannot silently re-point a disposition — the new block
    simply has no (or the wrong) marker and fails here.
    """
    unannotated = [
        f"{key[0]} block {key[1]} (line above the fence: "
        f"{DISCOVERED[key].marker.strip()!r})"
        for key in sorted(DISCOVERED) if MARKERS[key] is None]
    assert not unannotated, (
        f"fenced json/jsonc blocks whose preceding line is not a "
        f"well-formed annotation: {unannotated}. Put one of "
        "'<!-- validate: <entity> -->', '<!-- validate: <entity>#/<pointer> -->', "
        "'<!-- invalid: <RULE id> -->', '<!-- illustrative -->' directly above "
        f"the fence, per {_CONVENTION} — and move EXPECTED_DISPOSITIONS in "
        "this file to match.")


# Coverage is a conscious number: adding a block (or changing a disposition)
# must move this constant in the same change, so the validated surface never
# shrinks silently.
EXPECTED_DISPOSITIONS = {"validate": 9, "invalid": 0, "illustrative": 19}


def test_disposition_counts_are_conscious():
    found = Counter(m.kind for m in MARKERS.values() if m is not None)
    assert {k: found.get(k, 0) for k in EXPECTED_DISPOSITIONS} == EXPECTED_DISPOSITIONS, (
        f"disposition counts changed: {dict(found)} != {EXPECTED_DISPOSITIONS}. "
        "If intentional, update EXPECTED_DISPOSITIONS; fewer 'validate'/'invalid' "
        "entries means prose snippets lost validation coverage.")


# ---------------------------------------------------------------------------
# Comment stripping: a character scanner, because a naive regex would eat the
# `//` inside a string value like "https://schemas.analitiq.ai/..."
# ---------------------------------------------------------------------------

def _strip_jsonc(text: str) -> str:
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:  # escaped char, incl. \" — copy blindly
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue  # keep the newline itself
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            # Silently swallowing the rest would truncate the fragment and
            # grade a different document than the prose shows.
            assert end != -1, f"unterminated /* block comment at offset {i}"
            i = end + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def test_comment_stripper_respects_string_literals():
    """Guard the scanner: comment markers inside strings must survive —
    including after an escaped quote, where a state slip would end the string
    early and eat the `//` as a comment."""
    src = (
        '{\n'
        '  "url": "https://x/*not-a-comment*/y",  // trailing comment\n'
        '  "note": "a//b", /* block */ "n": 1,\n'
        '  "esc": "x\\"//y"\n'
        '}\n'
    )
    assert json.loads(_strip_jsonc(src)) == {
        "url": "https://x/*not-a-comment*/y", "note": "a//b", "n": 1,
        "esc": 'x"//y'}


def test_comment_stripper_rejects_unterminated_block_comment():
    with pytest.raises(AssertionError, match="unterminated /\\* block comment"):
        _strip_jsonc('{"a": 1 /* oops')


# ---------------------------------------------------------------------------
# Splicing
# ---------------------------------------------------------------------------

_ABSENT = object()


def _graft(fragment, host):
    """Resolve a prose fragment against the host value it illustrates.

    The placeholder rule: a fragment value that is an EMPTY object after
    comment stripping — e.g. ``"endpoint_ref": { /* see spec-endpoint-refs.md
    */ }`` — means *elided here*, and never overwrites the host's value; the
    host's real value is kept. (An empty ARRAY is not a placeholder: ``[]`` is
    content the fragment states, and is graded as such.) Everything else the
    fragment states is taken verbatim: a non-empty fragment container
    contributes exactly the keys and items it spells out (recursing only so
    nested placeholders still resolve), never a blend with host keys. Blending
    would manufacture hybrid documents no prose ever showed — e.g. a host
    assignment's ``expression`` body surviving inside a fragment's
    ``constant`` assignment value, which the closed contract models reject —
    and would grade the blend, not the prose.
    """
    if isinstance(fragment, dict):
        if not fragment:
            return fragment if host is _ABSENT else host
        host_map = host if isinstance(host, dict) else {}
        return {k: _graft(v, host_map.get(k, _ABSENT)) for k, v in fragment.items()}
    if isinstance(fragment, list):
        host_list = host if isinstance(host, list) else []
        return [
            _graft(item, host_list[i] if i < len(host_list) else _ABSENT)
            for i, item in enumerate(fragment)
        ]
    return fragment


def test_graft_replaces_without_blending():
    """A non-empty fragment container contributes exactly its own keys."""
    host = {"kind": "expression", "expression": {"op": "get", "path": ["id"]}}
    fragment = {"kind": "constant", "constant": {"value": "x"}}
    assert _graft(fragment, host) == fragment  # no "expression" survivor


def test_graft_placeholder_resolves_to_host_value():
    """`{}` keeps the host's value — the whole value, nested content included."""
    host = {"ref": {"scope": "connection", "ids": {"connection_id": "c1"}},
            "untouched": 1}
    assert _graft({"ref": {}, "n": 2}, host) == {
        "ref": {"scope": "connection", "ids": {"connection_id": "c1"}}, "n": 2}


def test_graft_never_leaks_the_absent_sentinel():
    """A placeholder with no host counterpart stays `{}` — the sentinel is
    internal and must never reach the spliced document."""
    grafted = _graft({"a": {}, "b": [{}], "c": [1, 2]}, {"unrelated": 1})
    assert grafted == {"a": {}, "b": [{}], "c": [1, 2]}
    json.dumps(grafted)  # would explode on a leaked _ABSENT


def _splice(host_doc: dict, fragment, segments: list[str] | None) -> dict:
    """Return the host document with the fragment spliced in.

    ``segments is None``: the fragment is an object whose top-level keys are
    merged into the host document (host keys the fragment omits survive —
    ``$schema``, ids, the untouched sections). Otherwise ``segments`` is the
    marker pointer's path (numeric segments index into arrays) and the
    fragment replaces the value there. Either way ``_graft`` resolves
    placeholders against the host value being replaced.
    """
    doc = json.loads(json.dumps(host_doc))  # deep copy
    if segments is None:
        for key, value in fragment.items():
            doc[key] = _graft(value, doc.get(key, _ABSENT))
        return doc
    parent = doc
    *steps, last = segments
    for seg in steps:
        parent = parent[int(seg)] if isinstance(parent, list) else parent[seg]
    if isinstance(parent, list):
        parent[int(last)] = _graft(fragment, parent[int(last)])
    else:
        parent[last] = _graft(fragment, parent.get(last, _ABSENT))
    return doc


# ---------------------------------------------------------------------------
# What the marker cannot express: the host document per entity, and the
# pinned placeholder positions
# ---------------------------------------------------------------------------

# Which spec skill's examples/ directory hosts each entity's documents —
# derived from the sibling suite's mapping, never restated.
ENTITY_SKILL = {entity: skill for skill, entity in test_examples.SKILL_ENTITY.items()}

# The one bundled example each entity's fragments splice into. Chosen so the
# host tolerates the prose's shapes: the postgresql connection example lines
# up with the envelope fragment's connector_id and input names, and the
# incremental-upsert stream's database destination accepts the upsert +
# conflict_keys write the destination prose shows.
HOST_EXAMPLE = {
    "pipeline": "manual-api-to-db.example.json",
    "stream": "db-incremental-upsert.example.json",
    "connection": "db.example.json",
}

# Every `{}` placeholder position across the spliced fragments, keyed by
# (file, marker target) — marker targets, unlike block indexes, do not shift
# when a block is added above another. Same-file blocks sharing a target pool
# their positions. Pinning the exact set means emptying a real fragment value
# (a whole `write`, a `schedule`) cannot silently degrade that splice into
# grading the host against itself: the new `{}` position shows up here.
EXPECTED_PLACEHOLDERS = {
    ("skills/stream-spec/spec-destinations.md", "stream#/destinations"):
        {"destinations.0.endpoint_ref"},
    ("skills/stream-spec/spec-source.md", "stream#/source"):
        {"source.endpoint_ref"},
}


def _placeholder_paths(fragment, prefix: str = "") -> set[str]:
    """Dotted host-coordinate paths of every `{}` in the fragment."""
    found: set[str] = set()
    if isinstance(fragment, dict):
        if not fragment:
            return {prefix or "<top level>"}
        for k, v in fragment.items():
            found |= _placeholder_paths(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(fragment, list):
        for i, v in enumerate(fragment):
            found |= _placeholder_paths(v, f"{prefix}.{i}" if prefix else str(i))
    return found


def _resolve_fragment(marker: Marker, body: str, label: str):
    """-> (fragment, pointer segments or None), unwrapped and sanity-checked."""
    fragment = json.loads(_strip_jsonc(body))
    segments = None
    if marker.pointer:
        segments = [seg.replace("~1", "/").replace("~0", "~")
                    for seg in marker.pointer.lstrip("/").split("/")]
        # The convention lets a fragment show its enclosing key for context;
        # the pointer names the deepest shown node, so unwrap before splicing.
        if isinstance(fragment, dict) and list(fragment) == [segments[-1]]:
            fragment = fragment[segments[-1]]
    assert fragment != {}, (
        f"{label}: the fragment is a single empty object, which the "
        "placeholder rule resolves to the host's own value — the splice would "
        "grade the host against itself and prove nothing. Show at least one "
        "real key, or mark the block illustrative.")
    if segments is None:
        assert isinstance(fragment, dict), (
            f"{label}: a pointer-less marker merges top-level keys, so the "
            "fragment must be an object; use the validate: <entity>#/<pointer> "
            "form for a non-object fragment.")
    return fragment, segments


def _grading_entity(marker: Marker, label: str) -> str:
    """The adapter entity a block grades as.

    A ``validate:`` marker states it. An ``invalid:`` marker states only the
    rule id; the registry's ``scope`` field supplies the entity —
    which also makes a dangling rule id fail the build, the same property a
    citation carries (plugin-prose rung 1). ``invalid:`` blocks target a
    sub-shape by showing its enclosing key (the wrapped-context form), which
    the top-level merge places for them.
    """
    if marker.kind == "invalid":
        from analitiq.contracts.shared.rules import all_rules
        rules = {rule.id: rule for rule in all_rules()}
        assert marker.rule in rules, (
            f"{label}: '<!-- invalid: {marker.rule} -->' names no rule in the "
            "rule registry — a dangling id pins nothing.")
        # Boundary translation: the registry spells resources hyphenated
        # (database-endpoint), the validator adapter spells entities with
        # underscores (database_endpoint). Without it, a hyphenated resource
        # would surface as a misdirecting KeyError deeper in the splice.
        # A rule may bind more than one artifact kind; a snippet grades ONE
        # document, so resolve to the scope that names a hosted entity.
        scopes = rules[marker.rule].scopes
        hosted = [s for s in scopes if s.replace("-", "_") in ENTITY_SKILL]
        assert len(hosted) < 2, (
            f"{label}: '<!-- invalid: {marker.rule} -->' binds {hosted}, so "
            "which document this block grades is ambiguous — use a 'validate:' "
            "marker naming the one the block carries.")
        entity = (hosted[0] if hosted else scopes[0]).replace("-", "_")
    else:
        entity = marker.entity
    assert entity in ENTITY_SKILL, (
        f"{label}: grades as entity {entity!r}, which no spec skill's "
        "examples are authored as; add the skill -> entity pair to "
        "test_examples.SKILL_ENTITY (with a validated examples/ tree) before "
        "a prose block can grade as it.")
    assert entity in HOST_EXAMPLE, (
        f"{label}: grades as entity {entity!r}, which has no host in "
        f"HOST_EXAMPLE; add a HOST_EXAMPLE[{entity!r}] entry naming the "
        f"bundled {ENTITY_SKILL[entity]}/examples/ document its fragments "
        "splice into.")
    return entity


def _spliced_document(marker: Marker, body: str, label: str):
    """-> (entity, fragment, segments, spliced document)."""
    entity = _grading_entity(marker, label)
    fragment, segments = _resolve_fragment(marker, body, label)
    host_path = SKILLS / ENTITY_SKILL[entity] / "examples" / HOST_EXAMPLE[entity]
    assert host_path.is_file(), f"{label}: missing host example {host_path}"
    spliced = _splice(json.loads(host_path.read_text()), fragment, segments)
    return entity, fragment, segments, spliced


def _assert_block_upholds_marker(marker: Marker, body: str, label: str,
                                 tmp_path: Path) -> None:
    """Grade one validate/invalid block: splice, validate, judge per marker."""
    entity, fragment, segments, spliced = _spliced_document(marker, body, label)
    doc_path = tmp_path / "spliced.json"
    doc_path.write_text(json.dumps(spliced, indent=2))
    diagnostics = V.diagnostics_for(entity, doc_path)
    where = "<top level>" if marker.pointer is None else marker.pointer
    if marker.kind == "validate":
        assert diagnostics["passed"], (
            f"{label}, spliced into {HOST_EXAMPLE[entity]} at {where}, "
            f"does not validate as {entity}: "
            + "; ".join(f"{f['path']}: {f['message']}"
                        for f in diagnostics["findings"])
            + " — the host validates on its own (test_examples.py), so either "
              "the prose teaches an invalid shape or the marker's "
              "entity/pointer is wrong.")
        if segments is None:
            # The merge lets host keys the fragment omits survive, so a
            # fragment silently decayed to one key would ride the host's
            # completeness and stay green. The pointer-less form claims
            # document shape, not a patch: the bare fragment must also
            # validate standalone, through the same adapter.
            alone_path = tmp_path / "standalone.json"
            alone_path.write_text(json.dumps(fragment, indent=2))
            alone = V.diagnostics_for(entity, alone_path)
            assert alone["passed"], (
                f"{label} does not validate as {entity} standalone: "
                + "; ".join(f"{f['path']}: {f['message']}"
                            for f in alone["findings"])
                + " — a pointer-less 'validate:' block must be a complete "
                  "document on its own; use validate: <entity>#/<pointer> "
                  "for a partial fragment.")
    else:
        # The diagnostics envelope carries Pydantic messages, not rule ids, so
        # the rule id in the marker is declared intent that review checks; the
        # gate can only assert the failure itself.
        assert not diagnostics["passed"], (
            f"{label} is marked '<!-- invalid: {marker.rule} -->' but the "
            f"spliced document VALIDATES as {entity} — a deliberately "
            "wrong example that rots into valid is the most misleading rot "
            "there is. Fix the block so it still breaks the rule, or "
            "re-annotate it.")


# ---------------------------------------------------------------------------
# The gate over the real tree
# ---------------------------------------------------------------------------

GRADED = [
    pytest.param(key, MARKERS[key], id=f"{key[0]}#{key[1]}")
    for key in sorted(DISCOVERED)
    if MARKERS[key] is not None and MARKERS[key].kind in ("validate", "invalid")
]

PARSE_ONLY = [pytest.param(key, id=f"{key[0]}#{key[1]}") for key in sorted(DISCOVERED)]


@pytest.mark.parametrize("key", PARSE_ONLY)
def test_every_block_parses_as_json(key):
    """Free coverage for every disposition, illustrative included: a fence
    tagged json/jsonc claims to hold JSON(C). If an author wants unparseable
    pseudo-JSON, the fence must carry a different (or no) language tag —
    which also removes it from this gate's discovery."""
    try:
        json.loads(_strip_jsonc(DISCOVERED[key].body))
    except (AssertionError, ValueError) as exc:
        pytest.fail(
            f"{key[0]} block {key[1]} is fenced as json/jsonc but does not "
            f"parse after comment stripping: {exc}. Fix the snippet, or "
            "re-tag the fence if pseudo-JSON is intended.")


@pytest.mark.parametrize("key,marker", GRADED)
def test_block_upholds_its_marker(key, marker, tmp_path):
    _assert_block_upholds_marker(
        marker, DISCOVERED[key].body, f"{key[0]} block {key[1]}", tmp_path)


def test_pointerless_fragment_must_validate_standalone(tmp_path):
    """A pointer-less fragment decayed to a single key merges green (the host
    supplies everything else) — the standalone grade is what catches it."""
    marker = _parse_marker("<!-- validate: connection -->")
    with pytest.raises(AssertionError, match="standalone"):
        _assert_block_upholds_marker(
            marker, '{"display_name": "decayed"}', "synthetic", tmp_path)


def test_placeholder_accounting_is_pinned():
    collected: dict[tuple[str, str], set[str]] = {}
    for key in sorted(DISCOVERED):
        marker = MARKERS[key]
        if marker is None or marker.kind != "validate":
            continue
        fragment, segments = _resolve_fragment(
            marker, DISCOVERED[key].body, f"{key[0]} block {key[1]}")
        paths = _placeholder_paths(fragment, ".".join(segments or []))
        if paths:
            collected.setdefault((key[0], marker.target), set()).update(paths)
    assert collected == EXPECTED_PLACEHOLDERS, (
        f"`{{}}` placeholder positions changed: {collected} != "
        f"{EXPECTED_PLACEHOLDERS}. A new position means a fragment value was "
        "emptied — that part of the prose is no longer graded (the host's own "
        "value fills it in). Update EXPECTED_PLACEHOLDERS only for a "
        "deliberate elision.")


# ---------------------------------------------------------------------------
# The `invalid:` disposition, exercised synthetically — no prose block uses it
# yet (EXPECTED_DISPOSITIONS pins that), so the machinery is proven here.
# ---------------------------------------------------------------------------

def test_invalid_disposition_requires_the_failure(tmp_path):
    marker = _parse_marker("<!-- invalid: RULE-PIPE-002 -->")
    assert marker == Marker("invalid", None, None, "RULE-PIPE-002")
    # The registry's `resource` supplies the entity (pipeline); the wrapped
    # `schedule` key places the fragment. Manual-type schedules admit no
    # interval_minutes, so this block upholds its marker by failing...
    _assert_block_upholds_marker(
        marker, '{"schedule": {"type": "manual", "interval_minutes": 5}}',
        "synthetic", tmp_path)
    # ...a block marked invalid that VALIDATES is itself the defect...
    with pytest.raises(AssertionError, match="rots into valid"):
        _assert_block_upholds_marker(
            marker, '{"schedule": {"type": "manual"}}', "synthetic", tmp_path)
    # ...and a rule id the registry does not know pins nothing, loudly.
    with pytest.raises(AssertionError, match="dangling id"):
        _assert_block_upholds_marker(
            _parse_marker("<!-- invalid: RULE-PIPE-999 -->"),
            '{"schedule": {"type": "manual"}}', "synthetic", tmp_path)


def test_invalid_disposition_translates_hyphenated_registry_resources():
    """The registry spells resources hyphenated; the validator adapter spells
    entities with underscores. `_grading_entity` must translate at that
    boundary — the failure for an unhosted entity is then the actionable
    membership assertion naming the underscore spelling, never a misdirecting
    KeyError on the hyphenated one."""
    from analitiq.contracts.shared.rules import all_rules
    # Single-scope only: a rule binding a hosted scope too would resolve to
    # that one in `_grading_entity` and never reach the assertion under test.
    rule = next(
        (r for r in all_rules()
         if len(r.scopes) == 1 and "-" in r.scopes[0]
         and r.scopes[0].replace("-", "_") not in HOST_EXAMPLE),
        None)
    if rule is None:  # every hyphenated resource gained a host: real blocks cover it
        pytest.skip("no hyphenated-resource rule without a host in the registry")
    marker = _parse_marker(f"<!-- invalid: {rule.id} -->")
    with pytest.raises(AssertionError,
                       match=re.escape(repr(rule.scopes[0].replace("-", "_")))):
        _grading_entity(marker, "synthetic")
