"""Every way the registry compiler refuses a record, exercised once each.

`scripts/render_rules.py` is the gate: `rules/records/*.yaml` reaches the
shipped `rules.json`, the rendered plugin prose and the enforcer census only by
passing it. CI runs it on the live registry, where every record is valid — so
without this module each refusal path executes on every build and none of them
ever fires. A refusal that stopped working would be indistinguishable from one
that works, and it fails *open*: the broken record compiles and ships.

That is not hypothetical. The compiler once globbed a directory that had moved,
found nothing, wrote a valid empty registry and exited 0 — every consumer
reading "no rules exist" from a green build.

Each test writes one deliberately broken record into a registry of its own and
asserts the compiler names it. The assertions match on the identifier at fault
(a key, an id, a filename), never on the sentence around it, so rewording a
diagnostic does not redden the build.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "render_rules.py"

CONTRACTS = "analitiq.contracts"


def _load_script():
    """Import the compiler by path — `scripts/` is not an installed package."""
    spec = importlib.util.spec_from_file_location("render_rules", SCRIPT_PATH)
    assert spec and spec.loader, f"cannot load {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RR = _load_script()


#: A record that passes every check, as YAML lines keyed by field. Each test
#: mutates one key: what it asserts is that this record minus one property is
#: refused, which is only meaningful while the unmutated record is accepted —
#: `test_the_baseline_record_is_accepted` pins that.
BASELINE = {
    "id": "RULE-TEST-001",
    "statement": "A connector MUST declare a default transport.",
    "tier": "structural",
    "severity": "error",
    "scopes": "[connector]",
    "validator": "null",
    "owners": "[connector-plugin]",
    "rationale": "Stated here because the corpus needs a record to mutate.",
    "status": "active",
    "superseded_by": "null",
}


def _write(directory: Path, *, name: str | None = None, **overrides: str) -> Path:
    """Write the baseline record with `overrides` applied, and return its path.

    An override whose value is `None` drops the key entirely, which is how the
    tests reach the "field absent" refusals as distinct from "field wrong".
    """
    fields = {**BASELINE, **overrides}
    body = "".join(f"{k}: {v}\n" for k, v in fields.items() if v is not None)
    path = directory / (name or f"{fields.get('id', 'RULE-TEST-001')}.yaml")
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """An empty registry directory the compiler reads instead of the real one."""
    directory = tmp_path / "records"
    directory.mkdir()
    monkeypatch.setattr(RR, "RULES_DIR", directory)
    monkeypatch.setattr(RR, "REPO_ROOT", tmp_path)
    return directory


def _refusal(registry_dir: Path) -> str:
    """Compile, require a refusal, and hand back what it said."""
    with pytest.raises(SystemExit) as exc:
        RR.load_registry()
    return str(exc.value)


# --- The baseline itself ----------------------------------------------------


def test_the_baseline_record_is_accepted(registry):
    """Every other test here is a mutation of this record.

    Without this, a baseline that had gone invalid for some unrelated reason
    would satisfy all of them — each would still see a refusal, just not the
    one it names.
    """
    _write(registry)
    records = RR.load_registry()
    assert [r.id for r in records] == ["RULE-TEST-001"]


# --- What the record itself refuses (RuleRecord) ----------------------------


def test_a_descriptive_tier_is_refused(registry):
    _write(registry, tier="descriptive")
    assert "descriptive" in _refusal(registry)


@pytest.mark.parametrize("field", ["tier", "severity", "status"])
def test_a_value_outside_a_closed_vocabulary_is_refused(registry, field):
    _write(registry, **{field: "nonsense"})
    assert field in _refusal(registry)


@pytest.mark.parametrize("field", ["statement", "rationale"])
def test_prose_that_is_blank_is_refused(registry, field):
    _write(registry, **{field: '""'})
    assert field in _refusal(registry)


def test_a_statement_without_an_rfc2119_keyword_is_refused(registry):
    _write(registry, statement="A connector declares a default transport.")
    assert "RFC 2119" in _refusal(registry)


@pytest.mark.parametrize("field", ["owners", "targets", "fields"])
def test_a_scalar_where_a_list_belongs_is_refused(registry, field):
    """`tuple("Foo")` is `('F', 'o', 'o')` — the shape that binds nothing."""
    _write(registry, **{field: "SqlBulkLoad"})
    assert field in _refusal(registry)


@pytest.mark.parametrize("field", ["owners", "targets", "fields"])
def test_a_list_of_non_names_is_refused(registry, field):
    _write(registry, **{field: "[1, 2]"})
    assert field in _refusal(registry)


@pytest.mark.parametrize("field", ["mechanism", "fixture_model"])
def test_a_list_where_one_name_belongs_is_refused(registry, field):
    _write(registry, **{field: "[literal_enum]"})
    assert field in _refusal(registry)


def test_a_record_naming_no_owner_is_refused(registry):
    _write(registry, owners="[]")
    assert "owners" in _refusal(registry) or "applies this rule" in _refusal(registry)


def test_an_unknown_scope_is_refused(registry):
    _write(registry, scopes="[nonsense]")
    assert "nonsense" in _refusal(registry)


def test_an_empty_scope_list_is_refused(registry):
    """A record naming no artifact kind renders into no file.

    `owners` decides which plugin's set a rule joins; `scopes` decides which
    file inside it. With neither the rule is owned by a plugin and reachable
    from none of its documents — cited in prose, resolvable nowhere.
    """
    _write(registry, scopes="[]")
    assert "artifact kind" in _refusal(registry)


def test_a_scalar_scope_is_refused(registry):
    """`scopes: connector` is a list of ten one-letter names to YAML's reader.

    Tuple-of-a-string is the failure that matches nothing and says so nowhere,
    which is why the record refuses the scalar rather than coercing it.
    """
    _write(registry, scopes="connector")
    assert "list of names" in _refusal(registry)


def test_a_repeated_scope_is_refused(registry):
    _write(registry, scopes="[connector, connector]")
    assert "repeats" in _refusal(registry)


def test_any_beside_a_named_scope_is_refused(registry):
    """`any` already covers every authored document.

    Naming it beside a specific kind states a narrower claim than the record
    makes, and the renderer would ignore the narrower half — so the record is
    refused instead of rendering something its author did not mean.
    """
    _write(registry, scopes="[any, connector]")
    assert "any" in _refusal(registry)


def test_a_rule_may_bind_two_artifact_kinds(registry):
    """The case the scalar could not express, and the reason for the list.

    A rule binding two kinds renders into both files, so both authors meet it.
    Under a scalar one of them silently did not.
    """
    _write(registry, scopes="[connector, api-endpoint]")
    records = RR.load_registry()
    assert records[0].scopes == ("connector", "api-endpoint")


def test_an_unknown_owner_is_refused(registry):
    _write(registry, owners="[marketing]")
    assert "marketing" in _refusal(registry)


@pytest.mark.parametrize("field", ["tier", "severity", "status", "mechanism"])
def test_a_value_outside_its_closed_vocabulary_is_refused(registry, field):
    """Every closed set the record carries, refused the same way.

    `mechanism` is why this is parametrized rather than written for the one
    that was missing. It was typed `str` and checked only for being a string,
    so a value a character off its spelling compiled, shipped and rendered —
    and stopped being graded, because the vocabulary guard and the
    no-restatement guard both select records by that spelling and the rendered
    reference prints a dash in place of the members. A vocabulary nothing
    rejects is a vocabulary in name.
    """
    _write(registry, **{field: "no_such_value"})
    assert "no_such_value" in _refusal(registry)


def test_a_validator_that_is_not_a_binding_is_refused(registry):
    _write(registry, validator='"ConnectorBase._validate"')
    assert "validator" in _refusal(registry)


def test_a_validator_naming_a_source_path_is_refused(registry):
    """The binding names what is imported, never a path standing in for it.

    A path was the earlier form. It shipped into this package's `rules.json`
    pointing at a tree no consumer has, and the lint reached the module by
    slicing the string rather than resolving it — so a path that had never
    existed produced an importable name and passed.
    """
    _write(registry, validator=f'"{CONTRACTS}/connector.py::ConnectorBase"')
    assert "connector.py" in _refusal(registry)


def test_a_validator_naming_a_prose_document_is_refused(registry):
    """A document an agent reads is not a binding, even when the file exists.

    The record ships to PyPI inside `rules.json`, where a repo path resolves
    for nobody, and `mechanized` would then report an applied rule to a
    consumer who cannot see, load or run the thing applying it. The rules an
    agent applies reach it by being rendered into the plugin prose it loads,
    which the plugin references and the reachability tests already keep
    honest. The path below is a real tracked file, so what is refused is the
    form and not a typo in it.
    """
    _write(registry, validator='".claude/rules/no-drift-surfaces.md"')
    assert "no-drift-surfaces.md" in _refusal(registry)


def test_a_retired_record_naming_no_successor_is_refused(registry):
    """Prose citing a retired id has to have somewhere to go."""
    _write(registry, status="retired")
    assert "retired" in _refusal(registry)


def test_a_record_superseding_itself_is_refused(registry):
    _write(registry, status="retired", superseded_by="RULE-TEST-001")
    assert "supersede" in _refusal(registry)


def test_an_unknown_key_is_refused_by_name(registry):
    """The typo class: a misspelt key must not read as an absent one."""
    _write(registry, fixture_models="ConnectorBase")
    assert "fixture_models" in _refusal(registry)


# --- What the registry as a whole refuses (load_registry) -------------------


def test_a_record_that_is_not_a_mapping_is_refused(registry):
    (registry / "RULE-TEST-001.yaml").write_text("- a\n- b\n", encoding="utf-8")
    assert "not a mapping" in _refusal(registry)


def test_a_filename_that_does_not_match_its_id_is_refused(registry):
    """A finding names an id; the id is how a reader finds the record."""
    _write(registry, name="RULE-TEST-002.yaml")
    assert "RULE-TEST-002.yaml" in _refusal(registry)


def test_an_empty_registry_is_refused(registry):
    """A glob that matches nothing compiles to a valid, empty document."""
    assert "no *.yaml records" in _refusal(registry)


def test_a_record_saved_as_yml_is_refused(registry):
    """The other spelling is skipped, not read — an absent record, silently."""
    _write(registry, name="RULE-TEST-001.yml")
    assert "RULE-TEST-001.yml" in _refusal(registry)


def test_a_duplicate_id_is_refused(registry):
    _write(registry)
    _write(registry, name="RULE-TEST-002.yaml")
    assert "duplicate id RULE-TEST-001" in _refusal(registry)


def test_reissuing_an_id_retired_before_the_registry_is_refused(registry):
    """Those ids are in archived findings; reusing one re-points them."""
    retired = sorted(RR.RETIRED_BEFORE_THE_REGISTRY)[0]
    _write(registry, id=retired, name=f"{retired}.yaml")
    assert retired in _refusal(registry)


# --- What resolving the validator binding refuses (_unresolved_validators) --


def test_a_validator_outside_the_published_namespace_is_refused(registry):
    """A dotted module resolves against whatever is importable.

    `os.path::join` is a real symbol on a real module and enforces nothing
    here, so the namespace is what keeps a binding pointing at code this repo
    owns.
    """
    _write(registry, validator='"os.path::join"')
    assert "outside" in _refusal(registry)


def test_a_validator_naming_an_absent_module_is_refused(registry):
    _write(registry, validator=f'"{CONTRACTS}.no_such_module::Thing"')
    assert "no_such_module" in _refusal(registry)


def test_a_validator_naming_an_absent_class_is_refused(registry):
    _write(registry, validator=f'"{CONTRACTS}.connector::NoSuchModel"')
    assert "NoSuchModel" in _refusal(registry)


def test_a_validator_naming_an_absent_member_is_refused(registry):
    _write(registry, validator=f'"{CONTRACTS}.connector::ConnectorBase._no_such_method"')
    assert "_no_such_method" in _refusal(registry)


def test_a_validator_naming_an_inherited_member_is_refused(registry):
    """`hasattr` alone resolves every pydantic and `object` member.

    A binding that lands on `dict` names a method the model certainly has and
    an enforcer it certainly does not, so the lint would report a live rule for
    one nothing applies.
    """
    _write(registry, validator=f'"{CONTRACTS}.connector::ConnectorBase.dict"')
    assert "dict" in _refusal(registry)


def test_a_validator_naming_a_real_enforcer_is_accepted(registry):
    """The other side of the check above — a live binding must still resolve.

    Without this, tightening the member lookup until it accepted nothing would
    satisfy every refusal test in this module.
    """
    _write(
        registry,
        validator=f'"{CONTRACTS}.connector::ConnectorBase._default_transport_declared"',
    )
    assert [r.id for r in RR.load_registry()] == ["RULE-TEST-001"]


def test_a_validator_naming_a_model_field_is_accepted(registry):
    """A structural rule binds the field carrying its `Literal` or pattern."""
    _write(registry, validator=f'"{CONTRACTS}.connector::SqlBulkLoad.sqlalchemy"')
    assert [r.id for r in RR.load_registry()] == ["RULE-TEST-001"]


# --- The compiled output ----------------------------------------------------


def test_every_refusal_is_reported_together(registry):
    """One run names every broken record, not the first one it reaches.

    A compiler that stopped at the first problem would make fixing a registry
    an iteration per fault, which is how a batch of them gets fixed by deleting
    records instead.
    """
    _write(registry, name="RULE-TEST-002.yaml", owners="[marketing]")
    _write(registry, name="RULE-TEST-003.yaml", tier="nonsense")
    message = _refusal(registry)
    assert "marketing" in message and "tier" in message
