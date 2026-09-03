"""Every fenced json/jsonc snippet in this plugin's prose must uphold the
disposition its annotation declares.

`test_examples_validate.py` pins the bundled `examples/` trees, but creator
agents copy shapes from the fenced blocks inline in the plugin's `**/*.md` —
skills and agent definitions alike — and those are fragments (an `auth` block,
one pagination strategy, a write request), so no complete-document gate ever
sees them. This suite closes that hole over the WHOLE plugin tree, and it is
the extraction gate the annotation convention promised: every inline fence
carries an HTML comment directly above it declaring its verification contract
(`.claude/rules/plugin-prose.md` § "The annotation convention", the normative
home for both plugins). The gate classifies each block FROM that marker — there
is no hand-maintained registry of dispositions to drift from the prose. What
follows is how THIS gate grades each marker:

* ``<!-- validate: <resource> -->`` — the block claims to be a complete
  document: graded on its own where the registry below marks it STANDALONE,
  and otherwise by placing its top-level keys in a host.
* ``<!-- validate: <resource>#/<json-pointer> -->`` — the fragment replaces the
  host's value at that pointer, and the spliced document must validate. A
  fragment may show its enclosing key for context, braces or not
  (``"auth": { … }``); the pointer names the deepest shown node, and the gate
  unwraps the key before splicing.
* ``<!-- invalid: <RULE id> -->`` — deliberately wrong; the graded document
  must FAIL validation (a "don't do this" example that rots into valid is the
  most misleading rot there is). The marker carries a rule id and no pointer,
  so the registry's ``scope`` names the resource and the block's keys are
  merged into its host.
* ``<!-- illustrative -->`` — outside the published contract's validation
  surface; exempt from splicing, but must still parse as JSON(C).

Because every host is validated clean first (`test_host_validates_clean`), a
post-splice failure indicts the fragment: the prose is teaching a shape the
contract rejects. A discovered block with no parseable marker fails loudly —
markers travel with their blocks, so inserting, reordering, or swapping blocks
can never silently re-point a disposition.

**Hosts are keyed by (file, marker target), not by resource.** A connector
fragment needs a host carrying the context its pointer names, and no single
connector does: an `oauth2_client_credentials` block whose `token_exchange`
sets ``transport_ref: "auth"`` needs a connector declaring an `auth` transport,
while a header fragment needs one declaring `transports.api.headers`. Where the
plugin ships an example carrying the context, that example is the host — a
splice failure then indicts the prose or the archetype's divergence from it.
Where it ships none (no bundled endpoint declares a write operation, and each
pagination strategy needs its own declared params), the host is a fixture under
`fixtures/prose-hosts/`: a frame the prose is graded in, never an archetype,
which is why it lives here and not in the plugin tree. A graded endpoint is
staged under the filename its own `endpoint_id` names — a fragment cannot state
a filename, so pinning one would grade the host — leaving `endpoint-id-locator`
to hold the id against the path the fragment declares.

An empty object in a fragment is CONTENT here, graded as written — the sibling
gate over the pipeline plugin's tree
(`tests/pipeline_builder/test_prose_snippets.py`) reads `{}` as an elision
resolving to the host's value, which would be wrong for this prose:
`sql_capabilities.bulk_load: {}` is a real declaration (no bulk mechanism on
any transport family), and resolving it to the host's would grade a connector
the prose never showed.

Skips cleanly when the contract packages are absent, like the other guards, and
hard-fails instead under `DRIFT_REQUIRE_CONTRACT_MODELS=1`.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from _pins import require_contract_models

require_contract_models("analitiq.contracts", "analitiq.validator")

from analitiq.validator import validate_document  # noqa: E402

import test_examples_validate as ev  # noqa: E402  (sibling suite; pytest puts this dir on sys.path)

REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT = REPO_ROOT / "plugins" / "analitiq-connector-builder"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "prose-hosts"

#: The published resource each marker entity names, and the schema URL the
#: validator routes it by. The spellings are the resource slugs the convention
#: uses, so a marker naming a resource this gate cannot validate fails loudly
#: rather than being skipped.
ENTITY_SCHEMA = {
    "connector": ev.CONNECTOR_SCHEMA,
    "api-endpoint": ev.ENDPOINT_SCHEMA,
    "type-map-read": ev.TYPE_MAP_SCHEMAS["type-map-read.json"],
    "type-map-write": ev.TYPE_MAP_SCHEMAS["type-map-write.json"],
}


# ---------------------------------------------------------------------------
# Discovery: every ```json / ```jsonc fence under the plugin's **/*.md
# ---------------------------------------------------------------------------

# A fence opener: a run of >=3 backticks or tildes, then the info string.
# Matched against the line AFTER strip, so indented fences (idiomatic inside
# list steps) are seen.
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
    r"|validate:\s*(?P<entity>[a-z][a-z-]*)(?:#(?P<pointer>/\S+))?"
    r"|invalid:\s*(?P<rule>RULE-[A-Z]+-\d+)"
    r")\s*-->\s*$")

_CONVENTION = (
    "the annotation convention (.claude/rules/plugin-prose.md § 'The "
    "annotation convention')")


class Marker(NamedTuple):
    kind: str            # "validate" | "invalid" | "illustrative"
    entity: str | None   # validate: resource the graded document is judged as
    pointer: str | None  # validate: JSON pointer ("/a/b"); None = whole document
    rule: str | None     # invalid: the rule the block deliberately breaks

    @property
    def target(self) -> str:
        """The marker's own spelling of what it grades — stable across
        reindexing, so the host registry keys on it rather than on a block
        index."""
        if self.kind == "invalid":
            return str(self.rule)
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
        "'<!-- validate: <resource> -->', "
        "'<!-- validate: <resource>#/<pointer> -->', "
        "'<!-- invalid: <RULE id> -->', '<!-- illustrative -->' directly above "
        f"the fence, per {_CONVENTION} — and move EXPECTED_DISPOSITIONS in "
        "this file to match.")


# Coverage is a conscious number: adding a block (or changing a disposition)
# must move this constant in the same change, so the validated surface never
# shrinks silently.
EXPECTED_DISPOSITIONS = {"validate": 15, "invalid": 1, "illustrative": 7}


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
# Parsing a block: the wrapped-context forms
# ---------------------------------------------------------------------------

def _parse_body(body: str) -> Any:
    """JSON(C) the block holds, admitting the brace-less wrapped form.

    The convention lets a fragment show its enclosing key for context. Prose
    writes that both ways — `{"auth": {…}}` and the brace-less `"auth": {…}`
    that reads as a slice of the document it is lifted from — so a body that
    is not JSON on its own is retried wrapped in braces. Only the retry is
    tolerant: a body that parses neither way is a defect the caller reports.
    """
    stripped = _strip_jsonc(body)
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    return json.loads("{" + stripped + "}")


def test_parse_body_reads_the_brace_less_wrapped_form():
    assert _parse_body('"auth": {"type": "api_key"}') == {
        "auth": {"type": "api_key"}}


def _resolve_fragment(marker: Marker, body: str, label: str):
    """-> (fragment, pointer segments or None), unwrapped and sanity-checked."""
    fragment = _parse_body(body)
    segments = None
    if marker.pointer:
        segments = [seg.replace("~1", "/").replace("~0", "~")
                    for seg in marker.pointer.lstrip("/").split("/")]
        # The convention lets a fragment show its enclosing key for context;
        # the pointer names the deepest shown node, so unwrap before splicing.
        if isinstance(fragment, dict) and list(fragment) == [segments[-1]]:
            fragment = fragment[segments[-1]]
    assert fragment not in ({}, []), (
        f"{label}: the fragment is empty, so the grade would prove nothing. "
        "Show at least one real key, or mark the block illustrative.")
    return fragment, segments


# ---------------------------------------------------------------------------
# What the marker cannot express: the host each fragment is spliced into
# ---------------------------------------------------------------------------

#: The marker declares a complete document, so it needs no host: the fragment
#: IS the document the gate validates.
STANDALONE = "<standalone>"

#: (prose file, marker target) -> the document a fragment splices into, as a
#: repo-relative path (or STANDALONE). Keyed by target rather than by resource
#: because one connector cannot carry every context this prose teaches, and by
#: (file, target) rather than by block index so adding a block above another
#: never re-points a host. Same-file blocks sharing a target share the host.
HOSTS = {
    ("skills/connector-spec-api/spec-auth-flows.md",
     "connector#/transports/api/headers/Authorization"):
        "plugins/analitiq-connector-builder/skills/connector-spec-api/"
        "examples/api-key/api-key.example.json",
    # The block's `token_exchange` names `transport_ref: "auth"`, so the host
    # must declare an `auth` transport: the multi-origin example does.
    ("skills/connector-spec-api/spec-auth-flows.md", "connector#/auth"):
        "plugins/analitiq-connector-builder/skills/connector-spec-api/examples/"
        "oauth2-authorization-code/oauth2-authorization-code.example.json",
    ("skills/connector-spec-api/spec-transport.md",
     "connector#/transports/api/base_url"):
        "plugins/analitiq-connector-builder/skills/connector-spec-api/"
        "examples/api-key/api-key.example.json",
    ("skills/connector-spec-db/spec-sql-write-path.md",
     "connector#/sql_capabilities"):
        "plugins/analitiq-connector-builder/skills/connector-spec-db/"
        "examples/postgresql/postgresql.example.json",
    # Each strategy binds its own pagination params, and every declared param
    # must be bound by exactly one request binding — so no strategy can splice
    # into a bundled example, whose params serve the strategy it declares.
    ("skills/connector-spec-api/spec-pagination.md",
     "api-endpoint#/operations/read/pagination"):
        "tests/connector_builder/fixtures/prose-hosts/v1__items.json",
    ("skills/connector-spec-api/spec-replication.md",
     "api-endpoint#/operations/read/replication"):
        "tests/connector_builder/fixtures/prose-hosts/v1__items.json",
    # The fragment replaces the whole read operation, so the host carries only
    # the id the fragment's `request.path` derives.
    ("skills/connector-spec-api/spec-request-binding.md",
     "api-endpoint#/operations/read"):
        "tests/connector_builder/fixtures/prose-hosts/v1__accounts__invoices.json",
    ("skills/connector-spec-api/spec-request-binding.md",
     "api-endpoint#/operations/write/insert/request/body"):
        "tests/connector_builder/fixtures/prose-hosts/v1__invoices.json",
    ("skills/connector-spec-api/spec-request-binding.md",
     "api-endpoint#/operations/write/upsert/request"):
        "tests/connector_builder/fixtures/prose-hosts/contact.json",
    # The read fragments declare their own filter params, so no bundled example
    # can host them: every declared param must be bound by exactly one request
    # binding, and an example's params serve the operation it ships.
    ("skills/connector-spec-api/spec-filters.md",
     "api-endpoint#/operations/read"):
        "tests/connector_builder/fixtures/prose-hosts/v1__invoices.json",
    ("skills/connector-spec-api/spec-filters.md", "RULE-ENDP-066"):
        "tests/connector_builder/fixtures/prose-hosts/v1__invoices.json",
    ("skills/connector-spec-db/spec-type-maps.md", "type-map-write"): STANDALONE,
}


def _graded_targets() -> set[tuple[str, str]]:
    return {
        (key[0], marker.target)
        for key, marker in sorted(MARKERS.items())
        if marker is not None and marker.kind in ("validate", "invalid")
    }


def test_host_registry_covers_exactly_the_graded_blocks():
    """Both directions at once: a graded block with no host cannot be graded,
    and an entry for a block that no longer exists is a stale claim of
    coverage."""
    assert set(HOSTS) == _graded_targets(), (
        f"HOSTS does not match the graded blocks: missing "
        f"{sorted(_graded_targets() - set(HOSTS))}, stale "
        f"{sorted(set(HOSTS) - _graded_targets())}. Every 'validate:' / "
        "'invalid:' block needs the document its fragment splices into (or "
        "STANDALONE when the block is a complete document).")


# ---------------------------------------------------------------------------
# Splicing and grading
# ---------------------------------------------------------------------------

def _splice(host_doc: Any, fragment: Any, segments: list[str]) -> Any:
    """Return the host document with the fragment replacing the pointed node.

    Numeric segments index into arrays. The fragment is taken verbatim — an
    empty object is content, not an elision — so the graded document holds
    exactly what the prose shows at that position. Every segment ABOVE the
    last must already exist: a host missing the context the pointer names
    would otherwise be filled in with a stub, and the incomplete shape that
    results would be reported as a defect in the prose.
    """
    doc = json.loads(json.dumps(host_doc))  # deep copy
    parent = doc
    *steps, last = segments
    for i, seg in enumerate(steps):
        try:
            parent = parent[int(seg)] if isinstance(parent, list) else parent[seg]
        except (KeyError, IndexError, ValueError):
            raise AssertionError(
                f"the host does not carry {'/'.join(segments[:i + 1])}, so the "
                "pointer names a context it cannot supply — point the block at "
                "a host that declares it"
            ) from None
    if isinstance(parent, list):
        parent[int(last)] = fragment
    else:
        parent[last] = fragment
    return doc


def test_splice_replaces_verbatim_including_an_empty_object():
    host = {"a": {"b": {"keep": 1}}}
    assert _splice(host, {}, ["a", "b"]) == {"a": {"b": {}}}
    assert _splice(host, {"x": 2}, ["a", "b"]) == {"a": {"b": {"x": 2}}}


def test_splice_reports_a_host_missing_the_pointed_context():
    with pytest.raises(AssertionError, match="does not carry operations/write"):
        _splice({"operations": {"read": {}}}, {"x": 1},
                ["operations", "write", "insert"])


def _merge(host_doc: dict, fragment: dict) -> dict:
    """Return the host document with the fragment's top-level keys placed.

    The form an ``invalid:`` block needs: its marker carries a rule id and no
    pointer, so the block shows the enclosing key of the shape it breaks and
    the merge places it. Host keys the fragment omits survive — `$schema`, the
    id, the untouched operations — which is what makes a one-key fragment a
    legible "don't do this".
    """
    doc = json.loads(json.dumps(host_doc))  # deep copy
    doc.update(fragment)
    return doc


def test_merge_places_the_fragments_keys_and_keeps_the_rest():
    assert _merge({"$schema": "s", "a": 1}, {"a": 2}) == {"$schema": "s", "a": 2}


def _grading_entity(marker: Marker, label: str) -> str:
    """The published resource a block grades as.

    A ``validate:`` marker states it. An ``invalid:`` marker states only the
    rule id; the registry's ``scopes`` supply the resource — which also makes
    a dangling rule id fail the build, the same property a citation carries
    (`plugin-prose.md` rung 1).
    """
    if marker.kind == "invalid":
        from analitiq.contracts.shared.rules import all_rules
        rules = {rule.id: rule for rule in all_rules()}
        assert marker.rule in rules, (
            f"{label}: '<!-- invalid: {marker.rule} -->' names no rule in the "
            "rule registry — a dangling id pins nothing.")
        # A rule may bind more than one artifact kind, but a fence grades ONE
        # document, so the marker resolves to the scope naming a document this
        # gate can validate. Two of them is an ambiguous marker — a defect to
        # report rather than a coin to flip. None falls through to the
        # membership assertion below, which already says what to do instead.
        scopes = rules[marker.rule].scopes
        hosted = [s for s in scopes if s in ENTITY_SCHEMA]
        assert len(hosted) < 2, (
            f"{label}: '<!-- invalid: {marker.rule} -->' binds {hosted}, so "
            "which document this block grades is ambiguous — use a 'validate:' "
            "marker naming the one the block carries.")
        entity = hosted[0] if hosted else scopes[0]
    else:
        entity = marker.entity
    assert entity in ENTITY_SCHEMA, (
        f"{label}: grades as {entity!r}, which this gate cannot validate as a "
        f"document. Known resources: {sorted(ENTITY_SCHEMA)} — a rule scope "
        "that names no single published document (a type map's direction, a "
        "connector package) needs a 'validate:' marker pointing at the "
        "document that carries the shape instead.")
    return entity


def _findings(entity: str, document: Any, tmp_path: Path,
              host: Path | None) -> list[dict]:
    """Lay the document out the way the validator reads it, and grade it.

    Path and filename are inputs to the contract, not bookkeeping: a connector
    is validated from `definition/connector.json` so the sibling walk reaches
    its endpoints and type maps, an endpoint is named by the id it derives, and
    a type map is read as the direction its filename names.
    """
    if entity == "connector":
        assert host is not None, "a connector is graded inside its staged package"
        path = ev._stage(host.parent, tmp_path)  # siblings; the graded body wins
    elif entity == "api-endpoint":
        endpoints = tmp_path / "definition" / "endpoints"
        endpoints.mkdir(parents=True, exist_ok=True)
        path = endpoints / f"{document.get('endpoint_id', 'endpoint')}.json"
    else:
        path = tmp_path / f"{entity}.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return ev._errors(validate_document(
        document, doc_path=path.resolve(), schema_url=ENTITY_SCHEMA[entity]))


def _graded_document(marker: Marker, body: str, label: str,
                     host_ref: str) -> tuple[str, Any, Path | None]:
    """-> (entity, the document to validate, host path or None).

    Three forms, decided by what the marker and the registry state together: a
    pointer splices into the host; no pointer and no host means the block is
    the whole document; no pointer with a host merges the block's top-level
    keys into it.
    """
    entity = _grading_entity(marker, label)
    fragment, segments = _resolve_fragment(marker, body, label)
    if host_ref == STANDALONE:
        assert segments is None, (
            f"{label}: a pointer names a position inside a host, so it cannot "
            "be graded standalone — give it a host, or drop the pointer.")
        return entity, fragment, None
    host = REPO_ROOT / host_ref
    assert host.is_file(), f"{label}: missing host document {host_ref}"
    host_doc = json.loads(host.read_text(encoding="utf-8"))
    if segments is not None:
        return entity, _splice(host_doc, fragment, segments), host
    assert isinstance(fragment, dict), (
        f"{label}: a pointer-less block merges top-level keys into its host, "
        "so it must be an object; a non-object document is graded standalone "
        "(STANDALONE), and a fragment inside a host takes a pointer.")
    return entity, _merge(host_doc, fragment), host


def _assert_block_upholds_marker(marker: Marker, body: str, label: str,
                                 host_ref: str, tmp_path: Path) -> None:
    """Grade one validate/invalid block: splice, validate, judge per marker."""
    entity, document, host = _graded_document(marker, body, label, host_ref)
    errors = _findings(entity, document, tmp_path, host)
    where = "standalone" if host is None else f"spliced into {host.name}"
    if marker.kind == "validate":
        assert not errors, (
            f"{label}, {where}, does not validate as {entity}: "
            + "; ".join(f"{f['validator']} {f['path']}: {f['message']}"
                        for f in errors)
            + " — the host validates on its own, so either the prose teaches "
              "an invalid shape or the marker's resource/pointer is wrong.")
    else:
        assert errors, (
            f"{label} is marked '<!-- invalid: {marker.rule} -->' but the "
            f"document VALIDATES as {entity} — a deliberately wrong example "
            "that rots into valid is the most misleading rot there is. Fix "
            "the block so it still breaks the rule, or re-annotate it.")


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
        _parse_body(DISCOVERED[key].body)
    except (AssertionError, ValueError) as exc:
        pytest.fail(
            f"{key[0]} block {key[1]} is fenced as json/jsonc but does not "
            f"parse after comment stripping: {exc}. Fix the snippet, or "
            "re-tag the fence if pseudo-JSON is intended.")


def _host_entities() -> dict[str, set[str]]:
    """{host ref: the resources its blocks grade as} — read off the markers, so
    a host is never validated as a resource guessed from its filename."""
    hosts: dict[str, set[str]] = {}
    for key, marker in sorted(MARKERS.items()):
        if marker is None or marker.kind not in ("validate", "invalid"):
            continue
        ref = HOSTS.get((key[0], marker.target))
        if ref in (None, STANDALONE):
            continue
        hosts.setdefault(ref, set()).add(
            _grading_entity(marker, f"{key[0]} block {key[1]}"))
    return hosts


@pytest.mark.parametrize("host_ref", sorted(
    {ref for ref in HOSTS.values() if ref != STANDALONE}))
def test_host_validates_clean(host_ref, tmp_path):
    """The premise every splice rests on: a post-splice failure indicts the
    fragment only because the host was clean before it."""
    host = REPO_ROOT / host_ref
    document = json.loads(host.read_text(encoding="utf-8"))
    entities = _host_entities()[host_ref]
    for entity in sorted(entities):
        errors = _findings(entity, document, tmp_path / entity, host)
        assert not errors, f"{host_ref} as {entity}\n" + "\n".join(
            f"{f['validator']} {f['path']}: {f['message']}" for f in errors)


@pytest.mark.parametrize("key,marker", GRADED)
def test_block_upholds_its_marker(key, marker, tmp_path):
    _assert_block_upholds_marker(
        marker, DISCOVERED[key].body, f"{key[0]} block {key[1]}",
        HOSTS[(key[0], marker.target)], tmp_path)


# ---------------------------------------------------------------------------
# The `invalid:` disposition, exercised synthetically as well as by the prose
# that uses it, so a tree whose only negative example is deleted still proves
# the machinery grades one.
# ---------------------------------------------------------------------------

_TYPE_MAP_HOST = HOSTS[("skills/connector-spec-db/spec-type-maps.md",
                        "type-map-write")]
_ENDPOINT_HOST = HOSTS[("skills/connector-spec-api/spec-request-binding.md",
                        "api-endpoint#/operations/read")]


def test_validate_disposition_catches_a_shape_the_contract_refuses(tmp_path):
    """The property the whole gate rests on: a block the contract rejects
    fails, and the failure names the prose block."""
    with pytest.raises(AssertionError, match="does not validate as type-map-write"):
        _assert_block_upholds_marker(
            _parse_marker("<!-- validate: type-map-write -->"),  # no `native`
            '[{"match": "exact", "canonical": "Object"}]',
            "synthetic", _TYPE_MAP_HOST, tmp_path)


def _endpoint_rule() -> str:
    from analitiq.contracts.shared.rules import all_rules
    # Hosted by api-endpoint ALONE: a rule also hosted by a second document is
    # the ambiguous-marker case `_grading_entity` refuses, so it cannot serve
    # as this fixture.
    # StopIteration here is the failure signal working, not a case to guard:
    # the registry losing every api-endpoint-only rule is the defect to report.
    return next(  # skipcq: PTC-W0063
        r.id for r in all_rules()
        if [s for s in r.scopes if s in ENTITY_SCHEMA] == ["api-endpoint"]
    )


def test_invalid_disposition_requires_the_failure(tmp_path):
    """An `invalid:` block carries no pointer, so the registry's scope names
    the resource and the merge places its keys in the host."""
    marker = _parse_marker(f"<!-- invalid: {_endpoint_rule()} -->")
    assert marker.kind == "invalid" and marker.target == _endpoint_rule()
    # An id no path derives is what `endpoint-id-locator` refuses...
    _assert_block_upholds_marker(
        marker, '{"endpoint_id": "not__the__derived__handle"}',
        "synthetic", _ENDPOINT_HOST, tmp_path)
    # ...and a block marked invalid that VALIDATES is itself the defect.
    with pytest.raises(AssertionError, match="rots into valid"):
        _assert_block_upholds_marker(
            marker, '{"endpoint_id": "v1__accounts__invoices"}',
            "synthetic", _ENDPOINT_HOST, tmp_path)


def test_invalid_disposition_rejects_a_dangling_rule_id(tmp_path):
    with pytest.raises(AssertionError, match="no rule in the rule registry"):
        _assert_block_upholds_marker(
            _parse_marker("<!-- invalid: RULE-ENDP-999 -->"), "{}",
            "synthetic", _ENDPOINT_HOST, tmp_path)


def test_invalid_disposition_rejects_a_resource_with_no_document():
    """A rule scope naming no single published document (a connector package,
    a type map whose direction the id does not state) must fail on the
    resource, not deeper — a KeyError in the splice would misdirect."""
    from analitiq.contracts.shared.rules import all_rules
    rule = next(
        (r for r in all_rules()
         if not any(s in ENTITY_SCHEMA for s in r.scopes)), None)
    if rule is None:  # every scope gained a document: real blocks cover it
        pytest.skip("no rule scoped outside the published documents")
    with pytest.raises(AssertionError, match="cannot validate as a document"):
        _grading_entity(_parse_marker(f"<!-- invalid: {rule.id} -->"), "synthetic")
