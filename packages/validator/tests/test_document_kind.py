"""The caller names the document kind, and that name is what grades it.

One machinery decides what a document is: the caller says so. The package entry
point reads the kind off the key a document sits at; the single-document entry
point takes it as an argument. Nothing reads a document's body to work out what
it is, which is the property these tests hold — a document is graded as the kind
it was submitted under, whatever its content resembles and however little of it
is there.

That is the whole point of naming the kind. A document is submitted for grading
because its author does not know whether it is right, so its content is the
least reliable thing to identify it by: the defects worth catching are exactly
the ones that make a document stop resembling its own kind.
"""
import json

import pytest

from analitiq.contracts.connection import CONNECTION_SCHEMA_URL
from analitiq.contracts.connector import CONNECTOR_SCHEMA_URL
from analitiq.contracts.validation_requests import DOCUMENT_SCHEMA_NAMES
from analitiq.validator import PIPELINE_BUNDLE_KIND


def _errors(findings):
    # A `notApplicable` carries no `severity` at all (`rules/SCHEMA.md`).
    return [f for f in findings if f.get("severity") == "error"]


def test_every_published_document_schema_name_grades_a_document(validator):
    """A caller that reads the vocabulary off the contract can submit under any
    name in it, and no name in it is unreachable."""
    assert set(DOCUMENT_SCHEMA_NAMES) <= validator.document_kinds()


def test_an_assembled_run_is_a_kind_beside_the_published_names(validator):
    """One entry point grades everything a caller can submit. A bundle is the
    only kind no published document schema names, because it is assembled from
    documents rather than authored as one — so the kinds are the published
    names plus it, and a kind arriving from anywhere else is unaccounted for."""
    assert validator.document_kinds() == set(DOCUMENT_SCHEMA_NAMES) | {PIPELINE_BUNDLE_KIND}


def test_a_kind_outside_the_vocabulary_is_refused(validator):
    """A name no kind answers to is the caller's error, not the document's: it
    raises rather than becoming a finding about a document that may be fine."""
    with pytest.raises(ValueError, match="unknown document kind"):
        validator.validate_document({}, "connector-ish")


def test_a_connector_without_its_kind_field_is_graded_as_a_connector(validator):
    """The defect a submitted document most needs named is the field that makes
    it identifiable, so grading may not depend on that field being there.

    A connector that omits `kind` carries `connector_id`, which a connection
    carries too. Graded as the connector it was submitted as, it is told that
    `kind` is what it failed to supply; graded by resemblance it would be told
    its `$schema` names the wrong resource — a defect it does not have, in a
    document of a kind it is not.
    """
    doc = {"$schema": CONNECTOR_SCHEMA_URL, "connector_id": "stripe",
           "display_name": "Stripe"}
    [error] = _errors(validator.validate_document(doc, "connector"))
    assert "kind" in error["message"], error
    assert "$schema" not in error["message"], error


def test_a_connection_is_graded_as_a_connection(validator):
    """The same document body, submitted as the kind it is, passes — so the
    grading above is the connector model, not a document both kinds reject."""
    doc = {"$schema": CONNECTION_SCHEMA_URL, "connector_id": "stripe"}
    assert validator.validate_document(doc, "connection") == []


@pytest.mark.parametrize("kind", sorted(DOCUMENT_SCHEMA_NAMES))
def test_an_empty_document_is_graded_as_the_kind_it_was_submitted_as(validator, kind):
    """An object with nothing in it resembles no kind at all, which is what the
    body-reading machinery had no answer for. Every kind reaches its own
    validator and returns that validator's verdict — no name in the vocabulary
    is unreachable, and none of them grades by falling over, which the crash
    guard would report as a rule it could not evaluate."""
    findings = validator.validate_document({}, kind)
    assert _errors(findings), kind
    assert "check-crashed" not in {f["message_id"] for f in findings}, findings


def test_the_cli_requires_the_kind(validator_cli, tmp_path):
    """The CLI's caller states the kind for the same reason an in-process one
    does, so omitting it is a usage error — never a document validated as
    whatever it resembles."""
    doc = tmp_path / "doc.json"
    doc.write_text("{}")
    result = validator_cli.run("--document", str(doc))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "--kind" in result.stderr


def test_the_cli_grades_under_the_kind_it_is_given(validator_cli):
    """The kind reaches the grading, rather than being accepted and dropped."""
    doc = {"$schema": CONNECTOR_SCHEMA_URL, "connector_id": "stripe"}
    result = validator_cli.on_document(doc, kind="connector")
    assert result.returncode == 1
    assert "kind" in json.loads(result.stdout)["findings"][0]["message"], result.stdout
