"""Every shipped example must validate against the pinned contract.

The `examples/` trees are what creator agents read as authoring archetypes, so a
stale example is worse than no example — it teaches a shape the validator
rejects. (All three API examples were silently invalid when this guard was
added, including a `response.records` ref over a non-array node.)

A type-map rule written inline in skill prose is an example too, and the one
authoring agents reach first — so the templated pairs in the spec text are run
through the same models as the files under `examples/`.

Examples are laid out for readability (`<name>/<name>.example.json` beside its
type maps and `endpoints/`), not as an on-disk connector, so each is staged into
a `definition/` directory first — the layout the cross-file coverage checks walk.

Same environment contract as the other drift guards: skipped when the pinned
packages are absent, hard-failed in CI via `DRIFT_REQUIRE_CONTRACT_MODELS=1`.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from _pins import require_contract_models

require_contract_models("analitiq.contracts", "analitiq.validator")

from analitiq.validator import validate_document  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "plugins" / "analitiq-connector-builder" / "skills"
CONNECTOR_SCHEMA = "https://schemas.analitiq.ai/connector/latest.json"
ENDPOINT_SCHEMA = "https://schemas.analitiq.ai/api-endpoint/latest.json"
TYPE_MAP_SCHEMAS = {
    "type-map-read.json": "https://schemas.analitiq.ai/type-map-read/latest.json",
    "type-map-write.json": "https://schemas.analitiq.ai/type-map-write/latest.json",
}


def _example_dirs() -> list[Path]:
    return sorted(
        d
        for d in SKILLS_ROOT.glob("connector-spec-*/examples/*")
        if d.is_dir() and any(d.glob("*.example.json"))
    )


def _stage(example_dir: Path, dest_root: Path) -> Path:
    """Copy an example into the `definition/` layout the sibling walks expect."""
    definition = dest_root / "definition"
    definition.mkdir(parents=True)

    body = next(example_dir.glob("*.example.json"), None)  # skipcq: PTC-W0063
    if body is None:  # _example_dirs() filters for this, but fail usefully if staged directly
        raise FileNotFoundError(f"{example_dir} has no *.example.json to stage")
    shutil.copy(body, definition / "connector.json")
    for name in ("type-map-read.json", "type-map-write.json"):
        src = example_dir / name
        if src.exists():
            shutil.copy(src, definition / name)
    endpoints = example_dir / "endpoints"
    if endpoints.is_dir():
        shutil.copytree(endpoints, definition / "endpoints")
    return definition / "connector.json"


def _errors(findings: list[dict]) -> list[dict]:
    return [f for f in findings if f["severity"] == "error"]


#: An inline `native: "…"` / `canonical: "…"` field written in skill prose,
#: JSON-quoted exactly as it would appear in a rule.
_PROSE_FIELD_RE = re.compile(r"`(native|canonical):\s*(\"(?:[^\"\\]|\\.)*\")`")


def _prose_type_map_rules() -> list[tuple[Path, int, str, dict]]:
    """Templated type-map rules authored inline in connector skill prose.

    Scope is the pairs carrying `${…}`: a placeholder decides the direction
    (read renders the canonical, write renders the native), and the templated
    shape is the one whose validity is not readable by eye — an unbounded
    capture feeding a bounded parameter position looks perfectly ordinary. A
    pair with no placeholder is left to review; when one wants covering, give
    it a direction and extend the classifier rather than guessing here.
    """
    found: list[tuple[Path, int, str, dict]] = []
    for path in sorted(SKILLS_ROOT.glob("connector-spec-*/*.md")):
        fields = [
            (lineno, key, json.loads(raw))
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            )
            for key, raw in _PROSE_FIELD_RE.findall(line)
        ]
        for (line_a, key_a, val_a), (line_b, key_b, val_b) in zip(fields, fields[1:]):
            if key_a == key_b or line_b - line_a > 1:
                continue
            rule = {"match": "regex", key_a: val_a, key_b: val_b}
            templated = [k for k in ("native", "canonical") if "${" in rule[k]]
            if len(templated) != 1:
                continue
            direction = "write" if templated == ["native"] else "read"
            found.append((path, line_a, direction, rule))
    return found


def test_prose_type_map_rules_validate(tmp_path: Path) -> None:
    """A rule taught in prose must survive the model that judges the real one.

    Prose is copied verbatim by authoring agents, so an example the contract
    refuses ships a rule every author has to unlearn — and the `examples/`
    walks above never see it, because it lives in backticks rather than in a
    file.
    """
    rules = _prose_type_map_rules()
    by_direction = {direction for _, _, direction, _ in rules}
    assert by_direction == {"read", "write"}, (
        f"the prose extractor found {sorted(by_direction)} rules under "
        f"{SKILLS_ROOT} — a spec teaching both directions should yield both; "
        "the inline `native:`/`canonical:` shape it keys on has probably moved"
    )

    failures: list[str] = []
    for path, lineno, direction, rule in rules:
        map_path = tmp_path / f"type-map-{direction}.json"
        map_path.write_text(json.dumps([rule]), encoding="utf-8")
        findings = validate_document(
            [rule],
            doc_path=map_path.resolve(),
            schema_url=TYPE_MAP_SCHEMAS[f"type-map-{direction}.json"],
        )
        failures += [
            f"{path.relative_to(REPO_ROOT)}:{lineno} ({direction}) "
            f"{f['validator']}: {f['message']}"
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
def test_example_connector_validates(example_dir: Path, tmp_path: Path) -> None:
    doc_path = _stage(example_dir, tmp_path)
    document = json.loads(doc_path.read_text(encoding="utf-8"))

    findings = validate_document(
        document, doc_path=doc_path.resolve(), schema_url=CONNECTOR_SCHEMA
    )
    errors = _errors(findings)
    assert not errors, "\n".join(
        f"{f['validator']} {f['path']}: {f['message']}" for f in errors
    )


@pytest.mark.parametrize("example_dir", _example_dirs(), ids=lambda d: d.name)
def test_example_type_maps_validate(example_dir: Path, tmp_path: Path) -> None:
    """Validate each type map as a standalone document, under its own filename.

    This is the invocation `connector-schema-validator` documents (each map
    validated against its matching read/write schema URL, direction derived from
    the filename), so it should be exercised directly rather than only through
    the connector's sibling walk. It also localizes a failure to the map instead
    of surfacing it on the connector.

    It does NOT close the database read-map gap: rule-shape errors are already
    caught by the sibling walk, and neither level probes natives on a DB
    connector, so a wrong-case `exact` native still ships silently. That gap is
    documented in `spec-type-maps.md`, not covered here.
    """
    definition = _stage(example_dir, tmp_path).parent
    present = [(definition / name, url) for name, url in TYPE_MAP_SCHEMAS.items()
               if (definition / name).exists()]
    assert present, f"{example_dir.name} ships no type map"

    for map_path, schema_url in present:
        document = json.loads(map_path.read_text(encoding="utf-8"))
        findings = validate_document(
            document, doc_path=map_path.resolve(), schema_url=schema_url
        )
        errors = _errors(findings)
        assert not errors, f"{map_path.name}\n" + "\n".join(
            f"{f['validator']} {f['path']}: {f['message']}" for f in errors
        )


@pytest.mark.parametrize(
    "example_dir",
    [d for d in _example_dirs() if (d / "type-map-write.json").exists()],
    ids=lambda d: d.name,
)
def test_example_write_maps_render_bare_container_markers(
    example_dir: Path, tmp_path: Path
) -> None:
    """Every example write map must render the bare `Object`/`List` markers.

    The engine probes the write map with a destination column's `arrow_type`
    verbatim, and API-sourced documents carry the bare markers — a map without
    rules for them hard-errors the stream at configuration (issue #75). The
    coverage finding is warning-severity, so the error-only checks above would
    stay green if the rules were dropped; assert on the warning *naming* the
    markers instead of on warning absence, because an abbreviated example
    legitimately still warns about other families.
    """
    definition = _stage(example_dir, tmp_path).parent
    map_path = definition / "type-map-write.json"
    document = json.loads(map_path.read_text(encoding="utf-8"))
    findings = validate_document(
        document,
        doc_path=map_path.resolve(),
        schema_url=TYPE_MAP_SCHEMAS["type-map-write.json"],
    )
    named = [
        f["message"]
        for f in findings
        if f["validator"] == "type-map-write-coverage"
        and ("'Object'" in f["message"] or "'List'" in f["message"])
    ]
    assert not named, (
        f"{example_dir.name} write map has no rule rendering the bare "
        f"Object/List markers — the archetype teaches a map that fails on the "
        f"first API-sourced struct/array column:\n" + "\n".join(named)
    )


@pytest.mark.parametrize("example_dir", _example_dirs(), ids=lambda d: d.name)
def test_example_endpoints_validate(example_dir: Path, tmp_path: Path) -> None:
    """Endpoints must also hold up standalone.

    Validating from the connector walks siblings, but `endpoint-filename` and
    `endpoint-id-locator` are most direct here — and an endpoint is authored and
    validated on its own during the fan-out.
    """
    definition = _stage(example_dir, tmp_path).parent
    endpoint_files = sorted((definition / "endpoints").glob("*.json"))
    if not endpoint_files:
        pytest.skip(f"{example_dir.name} ships no endpoints")

    for endpoint_path in endpoint_files:
        document = json.loads(endpoint_path.read_text(encoding="utf-8"))
        findings = validate_document(
            document, doc_path=endpoint_path.resolve(), schema_url=ENDPOINT_SCHEMA
        )
        errors = _errors(findings)
        assert not errors, f"{endpoint_path.name}\n" + "\n".join(
            f"{f['validator']} {f['path']}: {f['message']}" for f in errors
        )
