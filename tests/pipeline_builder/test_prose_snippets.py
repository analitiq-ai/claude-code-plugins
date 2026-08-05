"""Every fenced json/jsonc snippet in this plugin's skill prose must validate.

`test_examples.py` pins the bundled `examples/*.example.json`, but creator
agents copy shapes from the fenced ``jsonc`` blocks inline in `skills/**/*.md`
too — and those are fragments (a `mapping` block, a `schedule` object, one
assignment), so no complete-document gate ever sees them. This suite closes
that hole by **splicing** each fragment into a bundled example already pinned
valid by the sibling suite, then validating the spliced document through the
same adapter (`diagnostics_for`). Because the host is known-valid, a
post-splice failure indicts the fragment: the prose is teaching a shape the
contract rejects.

Every discovered block must be classified in ``REGISTRY`` — as a splice or as
an explicit skip with a reason — and every registry entry must still name a
real block. Both directions fail loudly, so the gate can never go vacuous and
new prose snippets cannot ship unclassified.

Skips cleanly when the published packages are absent, like the other suites.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

ROOT = Path(__file__).resolve().parents[2] / "plugins" / "analitiq-pipeline-builder"
SKILLS = ROOT / "skills"
sys.path.insert(0, str(ROOT / "scripts"))
import validate as V  # noqa: E402

pytest.importorskip("analitiq.validator",
                    reason="requires: pip install -r requirements-dev.txt")


# ---------------------------------------------------------------------------
# Discovery: every ```json / ```jsonc fence under skills/**/*.md
# ---------------------------------------------------------------------------

_OPEN_FENCE = re.compile(r"^```(\S+)\s*$")
_CLOSE_FENCE = re.compile(r"^```\s*$")


def _discover_blocks() -> dict[tuple[str, int], str]:
    """Map (path relative to skills/, zero-based json[c]-block index) -> body.

    Every fence with an info string opens a block (so a ```python fence's
    contents can never be mistaken for a json fence's opener); only json/jsonc
    fences are collected, and the index counts those alone.
    """
    blocks: dict[tuple[str, int], str] = {}
    for md in sorted(SKILLS.rglob("*.md")):
        rel = md.relative_to(SKILLS).as_posix()
        index = 0
        lang: str | None = None
        body: list[str] = []
        for line in md.read_text().splitlines():
            if lang is None:
                m = _OPEN_FENCE.match(line)
                if m:
                    lang = m.group(1)
                    body = []
                continue
            if _CLOSE_FENCE.match(line):
                if lang in ("json", "jsonc"):
                    blocks[(rel, index)] = "\n".join(body)
                    index += 1
                lang = None
                continue
            body.append(line)
    return blocks


DISCOVERED = _discover_blocks()


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
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def test_comment_stripper_respects_string_literals():
    """Guard the scanner: comment markers inside strings must survive."""
    src = (
        '{\n'
        '  "url": "https://x/*not-a-comment*/y",  // trailing comment\n'
        '  "note": "a//b", /* block */ "n": 1\n'
        '}\n'
    )
    assert json.loads(_strip_jsonc(src)) == {
        "url": "https://x/*not-a-comment*/y", "note": "a//b", "n": 1}


# ---------------------------------------------------------------------------
# Splicing
# ---------------------------------------------------------------------------

_ABSENT = object()


def _graft(fragment, host):
    """Resolve a prose fragment against the host value it illustrates.

    The placeholder rule: a fragment value that is an EMPTY object after
    comment stripping — e.g. ``"endpoint_ref": { /* see spec-endpoint-refs.md
    */ }`` — means *elided here*, and never overwrites the host's value; the
    host's real value is kept. Everything else the fragment states is taken
    verbatim: a non-empty fragment container contributes exactly the keys and
    items it spells out (recursing only so nested placeholders still resolve),
    never a blend with host keys. Blending would manufacture hybrid documents
    no prose ever showed — e.g. a host assignment's ``expression`` body
    surviving inside a fragment's ``constant`` assignment value, which the
    closed contract models reject — and would grade the blend, not the prose.
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


def _splice(host_doc: dict, fragment, target: str | None) -> dict:
    """Return the host document with the fragment spliced in.

    ``target is None``: the fragment is an object whose top-level keys are
    merged into the host document (host keys the fragment omits survive —
    ``$schema``, ids, the untouched sections). A dotted ``target`` path
    (numeric segments index into arrays): the fragment replaces the value at
    that path. Either way ``_graft`` resolves placeholders against the host
    value being replaced.
    """
    doc = json.loads(json.dumps(host_doc))  # deep copy
    if target is None:
        assert isinstance(fragment, dict), "top-level merge needs an object fragment"
        for key, value in fragment.items():
            doc[key] = _graft(value, doc.get(key, _ABSENT))
        return doc
    parent = doc
    *steps, last = target.split(".")
    for seg in steps:
        parent = parent[int(seg)] if isinstance(parent, list) else parent[seg]
    if isinstance(parent, list):
        parent[int(last)] = _graft(fragment, parent[int(last)])
    else:
        parent[last] = _graft(fragment, parent.get(last, _ABSENT))
    return doc


# ---------------------------------------------------------------------------
# The registry: every fenced json/jsonc block, classified
# ---------------------------------------------------------------------------

class Splice(NamedTuple):
    entity: str          # adapter entity the spliced document validates as
    host: str            # example filename under the entity's spec skill
    target: str | None   # None = top-level merge; dotted path = replace there


class Skip(NamedTuple):
    reason: str          # why this block is not a contract-document fragment


# Which spec skill's examples/ directory hosts each entity's documents
# (the inverse of test_examples.py's SKILL_ENTITY, for the entities used here).
ENTITY_SKILL = {
    "pipeline": "pipeline-spec",
    "stream": "stream-spec",
    "connection": "connection-spec",
}

REGISTRY: dict[tuple[str, int], Splice | Skip] = {
    # -- connection-spec ----------------------------------------------------
    # A postgresql envelope fragment (parameters + secret_refs); the host is
    # the postgresql connection example, so connector_id and input names line up.
    ("connection-spec/spec-envelope.md", 0):
        Splice("connection", "db.example.json", None),
    ("connection-spec/spec-envelope.md", 1): Skip(
        "a .secrets/credentials.json template (env-var-keyed sidecar the user "
        "fills in), not a document authored against a published contract"),

    # -- pipeline-spec ------------------------------------------------------
    ("pipeline-spec/spec-streams-and-status.md", 0):
        Splice("pipeline", "manual-api-to-db.example.json", None),
    # The three schedule variants each replace the host's schedule wholesale,
    # so ADV-PIPE-002's per-type field admission is graded on exactly the
    # fields the prose shows.
    ("pipeline-spec/spec-schedule.md", 0):
        Splice("pipeline", "manual-api-to-db.example.json", "schedule"),
    ("pipeline-spec/spec-schedule.md", 1):
        Splice("pipeline", "manual-api-to-db.example.json", "schedule"),
    ("pipeline-spec/spec-schedule.md", 2):
        Splice("pipeline", "manual-api-to-db.example.json", "schedule"),

    # -- stream-spec --------------------------------------------------------
    # The destinations sketch carries an endpoint_ref placeholder and an
    # upsert + conflict_keys ["id"] write; the incremental-upsert example is
    # the host whose database destination tolerates exactly that write shape.
    ("stream-spec/spec-destinations.md", 0):
        Splice("stream", "db-incremental-upsert.example.json", None),
    ("stream-spec/spec-destinations.md", 1): Skip(
        "a bare conflict-keys array fragment (a single write.conflict_keys "
        "value, byte-equal to the host destination's own); the full write "
        "shape is graded via this file's block 0"),
    ("stream-spec/spec-mapping.md", 0):
        Splice("stream", "db-incremental-upsert.example.json", None),
    ("stream-spec/spec-validation-rules.md", 0):
        Splice("stream", "db-incremental-upsert.example.json",
               "mapping.assignments.0"),
    ("stream-spec/spec-source.md", 0):
        Splice("stream", "db-incremental-upsert.example.json", None),

    # -- pipeline-builder/references ---------------------------------------
    # Agent-to-agent I/O envelopes, defined by this plugin's own prose — not
    # documents authored against a published contract, so there is nothing
    # for the validator to grade them as.
    ("pipeline-builder/references/io-contracts.md", 0): Skip(
        "PipelineFacts — the researcher agent's output envelope, a "
        "plugin-internal shape, not a published-contract document"),
    ("pipeline-builder/references/io-contracts.md", 1): Skip(
        "MintedIdentities — orchestrator-local id bundle, a plugin-internal "
        "shape, not a published-contract document"),
    ("pipeline-builder/references/io-contracts.md", 2): Skip(
        "CreatorOutput — creator agents' output envelope, a plugin-internal "
        "shape, not a published-contract document"),
    ("pipeline-builder/references/io-contracts.md", 3): Skip(
        "CreatorOutput (unsupported-case variant) — plugin-internal shape, "
        "not a published-contract document"),
    ("pipeline-builder/references/io-contracts.md", 4): Skip(
        "Diagnostics — scripts/validate.py's own output envelope, a "
        "plugin-internal shape, not a published-contract document"),
    ("pipeline-builder/references/io-contracts.md", 5): Skip(
        "DriftVerdict — the drift classifier's output envelope, a "
        "plugin-internal shape, not a published-contract document"),
}

# Coverage is a conscious number: adding a snippet (or reclassifying a skip)
# must move this constant in the same change, so the splice surface never
# shrinks silently.
EXPECTED_SPLICE_COUNT = 9


# ---------------------------------------------------------------------------
# Bidirectional pinning — the anti-vacuity property
# ---------------------------------------------------------------------------

def test_every_discovered_block_is_classified():
    unclassified = sorted(set(DISCOVERED) - set(REGISTRY))
    stale = sorted(set(REGISTRY) - set(DISCOVERED))
    assert not unclassified, (
        f"fenced json/jsonc blocks with no REGISTRY entry: {unclassified}. "
        "Classify each in REGISTRY — as a Splice into a bundled example, or "
        "as a Skip with a reason it is not a contract-document fragment.")
    assert not stale, (
        f"REGISTRY entries whose block no longer exists: {stale}. "
        "Remove the stale entries (indexes shift when a block is added or "
        "removed above another in the same file).")


def test_splice_count_is_a_conscious_number():
    found = sum(isinstance(e, Splice) for e in REGISTRY.values())
    assert found == EXPECTED_SPLICE_COUNT, (
        f"splice-entry count changed: {found} != {EXPECTED_SPLICE_COUNT}. "
        "If intentional, update EXPECTED_SPLICE_COUNT; a shrink means a "
        "snippet lost validation coverage.")


# ---------------------------------------------------------------------------
# Validation: splice each fragment into its pinned-valid host and grade it
# ---------------------------------------------------------------------------

SPLICES = [
    pytest.param(key, entry, id=f"{key[0]}#{key[1]}")
    for key, entry in sorted(REGISTRY.items())
    if isinstance(entry, Splice)
]


@pytest.mark.parametrize("key,entry", SPLICES)
def test_spliced_snippet_validates(key, entry, tmp_path):
    body = DISCOVERED[key]
    fragment = json.loads(_strip_jsonc(body))

    host_path = SKILLS / ENTITY_SKILL[entry.entity] / "examples" / entry.host
    assert host_path.is_file(), (
        f"registry names a missing host example: {host_path}")
    host_doc = json.loads(host_path.read_text())

    spliced = _splice(host_doc, fragment, entry.target)
    doc_path = tmp_path / "spliced.json"
    doc_path.write_text(json.dumps(spliced, indent=2))

    diagnostics = V.diagnostics_for(entry.entity, doc_path)
    assert diagnostics["passed"], (
        f"snippet {key[0]} block {key[1]}, spliced into {entry.host} at "
        f"{entry.target or '<top level>'}, does not validate as "
        f"{entry.entity}: "
        + "; ".join(f"{f['path']}: {f['message']}" for f in diagnostics["findings"])
        + " — the host validates on its own (test_examples.py), so either the "
          "prose teaches an invalid shape or the registry's splice choice is "
          "wrong."
    )
