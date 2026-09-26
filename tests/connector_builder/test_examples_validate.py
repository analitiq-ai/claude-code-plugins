"""Every shipped example must validate against the pinned contract.

The `examples/` trees are what creator agents read as authoring archetypes, so a
stale example is worse than no example — it teaches a shape the validator
rejects. (All three API examples were silently invalid when this guard was
added, including a `response.records` ref over a non-array node.)

A type-map rule written inline in skill prose is an example too, and the one
authoring agents reach first — so the templated pairs in the spec text are run
through the same models as the files under `examples/`.

Examples are laid out for readability (`<name>/<name>.example.json` beside its
type map and `endpoints/`), so each file is re-keyed to the location a
connector package holds it at before the package is graded.

Same environment contract as the other drift guards: skipped when the pinned
packages are absent, hard-failed in CI via `DRIFT_REQUIRE_CONTRACT_MODELS=1`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from _pins import require_contract_models

require_contract_models("analitiq.contracts", "analitiq.validator")

from analitiq.contracts.type_map import TYPE_MAP_SCHEMA_URL  # noqa: E402
from analitiq.contracts.validation_requests import (  # noqa: E402
    ValidatePackageRequest,
    ValidateSingleDocumentRequest,
)
from analitiq.validator import validate_package, validate_single_document  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "plugins" / "analitiq-connector-builder" / "skills"


def _example_dirs() -> list[Path]:
    return sorted(
        d
        for d in SKILLS_ROOT.glob("connector-spec-*/examples/*")
        if d.is_dir() and any(d.glob("*.example.json"))
    )


CONNECTOR_KEY = "definition/connector.json"
TYPE_MAP_KEY = "definition/type-map.json"
ENDPOINTS_PREFIX = "definition/endpoints/"


def _package_documents(example_dir: Path) -> dict[str, Any]:
    """The example's documents keyed by the location a connector package
    holds each at."""
    body = next(example_dir.glob("*.example.json"), None)  # skipcq: PTC-W0063
    if body is None:  # _example_dirs() filters for this, but fail usefully if handed one directly
        raise FileNotFoundError(f"{example_dir} has no *.example.json body")
    documents: dict[str, Any] = {CONNECTOR_KEY: json.loads(body.read_text(encoding="utf-8"))}
    type_map = example_dir / "type-map.json"
    if type_map.exists():
        documents[TYPE_MAP_KEY] = json.loads(type_map.read_text(encoding="utf-8"))
    for endpoint in sorted((example_dir / "endpoints").glob("*.json")):
        documents[ENDPOINTS_PREFIX + endpoint.name] = json.loads(
            endpoint.read_text(encoding="utf-8"))
    return documents


def _package_findings(documents: dict[str, Any]) -> list[dict]:
    request = ValidatePackageRequest(
        package_kind="connector",
        documents={key: json.dumps(doc) for key, doc in documents.items()})
    return validate_package(request)["findings"]


def _document_findings(document: Any, document_kind: str) -> list[dict]:
    request = ValidateSingleDocumentRequest(
        document=json.dumps(document), document_kind=document_kind)
    return validate_single_document(request)["findings"]


def _errors(findings: list[dict]) -> list[dict]:
    return [f for f in findings if f["severity"] == "error"]


#: An inline `native_type: "…"` / `arrow_type: "…"` field written in skill prose,
#: JSON-quoted exactly as it would appear in a rule.
_PROSE_FIELD_RE = re.compile(r"`(native_type|arrow_type):\s*(\"(?:[^\"\\]|\\.)*\")`")


def _decode(raw: str) -> str | None:
    """The field's text as JSON reads it, or None when JSON cannot read it.

    A field shown in prose has to paste into a map verbatim, so text that is
    not a JSON string literal (a lone `\\d` the author did not double) is a
    prose defect the caller reports — not an exception out of the extractor.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _prose_type_map_rules() -> list[tuple[Path, int, str, dict]]:
    """Templated type-map rules authored inline in connector skill prose.

    Scope is the pairs carrying `${…}`: a placeholder decides the direction
    (read renders the canonical, write renders the native), and the templated
    shape is the one whose validity is not readable by eye — an unbounded
    capture feeding a bounded parameter position looks perfectly ordinary. A
    pair with no placeholder is left to review; when one wants covering, give
    it a direction and extend the classifier rather than guessing here.

    A field whose text JSON cannot read comes back with a None value, and the
    direction is None with it: the caller reports both as failures.
    """
    found: list[tuple[Path, int, str, dict]] = []
    for path in sorted(SKILLS_ROOT.rglob("*.md")):
        fields = [
            (lineno, key, _decode(raw))
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            )
            for key, raw in _PROSE_FIELD_RE.findall(line)
        ]
        for (line_a, key_a, val_a), (line_b, key_b, val_b) in zip(fields, fields[1:]):
            if key_a == key_b or line_b - line_a > 1:
                continue
            rule = {"match": "regex", key_a: val_a, key_b: val_b}
            if val_a is None or val_b is None:
                found.append((path, line_a, "unquotable", rule))
                continue
            templated = [k for k in ("native_type", "arrow_type") if "${" in rule[k]]
            if len(templated) != 1:
                continue
            direction = "write" if templated == ["native_type"] else "read"
            found.append((path, line_a, direction, rule))
    return found


def test_prose_type_map_rules_validate() -> None:
    """A rule taught in prose must survive the model that judges the real one.

    Prose is copied verbatim by authoring agents, so an example the contract
    refuses ships a rule every author has to unlearn — and the `examples/`
    walks above never see it, because it lives in backticks rather than in a
    file.
    """
    rules = _prose_type_map_rules()
    by_direction = {direction for _, _, direction, _ in rules}
    assert by_direction >= {"read", "write"}, (
        f"the prose extractor found {sorted(by_direction)} rules under "
        f"{SKILLS_ROOT} — a spec teaching every direction should yield a rule in each; "
        "the inline `native:`/`canonical:` shape it keys on has probably moved"
    )

    failures: list[str] = [
        f"{path.relative_to(REPO_ROOT)}:{lineno} shows a field JSON cannot "
        f"read, so it does not paste into a map: {rule}"
        for path, lineno, direction, rule in rules
        if direction == "unquotable"
    ]
    for path, lineno, direction, rule in rules:
        if direction == "unquotable":
            continue
        doc = {"$schema": TYPE_MAP_SCHEMA_URL, direction: [rule]}
        findings = _document_findings(doc, "type-map")
        failures += [
            f"{path.relative_to(REPO_ROOT)}:{lineno} ({direction}) "
            f"{f.get('rule')}: {f['message']}"
            for f in _errors(findings)
        ]
    assert not failures, "type-map rules taught in prose that the contract refuses:\n" + "\n".join(
        failures
    )


def test_every_example_dir_is_covered() -> None:
    """Guard the glob itself — no example may silently drop out of coverage.

    `_example_dirs()` selects on a `*.example.json` body, so a renamed body file
    would quietly remove that example from the parametrized tests below while
    still leaving examples discovered. Assert against the directory listing, not
    against emptiness.
    """
    all_dirs = {d for d in SKILLS_ROOT.glob("connector-spec-*/examples/*") if d.is_dir()}
    assert all_dirs, f"no example directories under {SKILLS_ROOT}"

    uncovered = sorted(str(d.relative_to(SKILLS_ROOT)) for d in all_dirs - set(_example_dirs()))
    assert not uncovered, (
        f"example directories with no `*.example.json` body: {uncovered} — these "
        "are skipped by every check below. Rename the body file to "
        "`<name>.example.json` or remove the directory."
    )


@pytest.mark.parametrize("example_dir", _example_dirs(), ids=lambda d: d.name)
def test_example_package_validates(example_dir: Path) -> None:
    findings = _package_findings(_package_documents(example_dir))
    errors = _errors(findings)
    assert not errors, "\n".join(
        f"{f.get('rule')} {f['path']}: {f['message']}" for f in errors
    )


@pytest.mark.parametrize("example_dir", _example_dirs(), ids=lambda d: d.name)
def test_example_type_map_validates(example_dir: Path) -> None:
    """Validate the type map as a single document.

    `connector-schema-validator` documents that request, so it is exercised
    directly rather than only inside the package. It also localizes a
    failure to the map instead of surfacing it among the package's findings.

    It does NOT close the database read-map gap: rule-shape errors are already
    caught by the package request, and neither request probes natives on a DB
    connector, so a wrong-case `exact` native still ships silently. That gap is
    documented in `spec-type-maps.md`, not covered here.
    """
    documents = _package_documents(example_dir)
    assert TYPE_MAP_KEY in documents, f"{example_dir.name} ships no type-map.json"

    findings = _document_findings(documents[TYPE_MAP_KEY], "type-map")
    errors = _errors(findings)
    assert not errors, "\n".join(
        f"{f.get('rule')} {f['path']}: {f['message']}" for f in errors
    )
    # An example teaching a lowercase literal no normalized native holds, or
    # a container rendered as a scalar, teaches a defect; those findings can
    # be warnings, which the error check above misses.
    taught_defects = [f for f in findings
                      if f.get("rule") in ("RULE-TMAP-014", "RULE-TMAP-002")]
    assert not taught_defects, "\n".join(
        f"{f.get('rule')} {f['path']}: {f['message']}" for f in taught_defects
    )


def _has_write_map(example_dir: Path) -> bool:
    return "write" in _package_documents(example_dir).get(TYPE_MAP_KEY, {})


@pytest.mark.parametrize(
    "example_dir",
    [d for d in _example_dirs() if _has_write_map(d)],
    ids=lambda d: d.name,
)
def test_example_write_maps_render_bare_container_markers(example_dir: Path) -> None:
    """Every example write map must render the bare `Object`/`List` markers.

    The engine probes the write map with a destination column's `arrow_type`
    verbatim, and API-sourced documents carry the bare markers — a map without
    rules for them hard-errors the stream at configuration. The
    coverage finding is warning-severity, so the error-only checks above would
    stay green if the rules were dropped; assert on the warning *naming* the
    markers instead of on warning absence, because an abbreviated example
    legitimately still warns about other families.
    """
    findings = _document_findings(_package_documents(example_dir)[TYPE_MAP_KEY], "type-map")
    named = [
        f["message"]
        for f in findings
        if f.get("rule") == "RULE-TMAP-017"
        and ("'Object'" in f["message"] or "'List'" in f["message"])
    ]
    assert not named, (
        f"{example_dir.name} write map has no rule rendering the bare "
        f"Object/List markers — the archetype teaches a map that fails on the "
        f"first API-sourced struct/array column:\n" + "\n".join(named)
    )


@pytest.mark.parametrize("example_dir", _example_dirs(), ids=lambda d: d.name)
def test_example_endpoints_validate(example_dir: Path) -> None:
    """Endpoints must also hold up as single documents.

    The package request grades them too, but an endpoint is authored and
    validated on its own during the fan-out, where only what one document
    settles — `RULE-ENDP-046` among it — can reject it.
    """
    endpoints = {key: doc for key, doc in _package_documents(example_dir).items()
                 if key.startswith(ENDPOINTS_PREFIX)}
    if not endpoints:
        pytest.skip(f"{example_dir.name} ships no endpoints")

    for key, document in endpoints.items():
        errors = _errors(_document_findings(document, "api-endpoint"))
        assert not errors, f"{key}\n" + "\n".join(
            f"{f.get('rule')} {f['path']}: {f['message']}" for f in errors
        )
