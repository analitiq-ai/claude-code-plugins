"""StreamSource.filters cross-check against its API-scope landing constraint.

An API-scope source's filters land through its endpoint's `filters` map,
which RULE-ENDP-071 holds to one landing site per field/operator pair. This
file pins the stream-side half of that fact (RULE-STRM-041): a stream
document can't declare two entries for the same pair, because whichever the
endpoint applies last would silently win.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from analitiq.contracts.stream import StreamSource


def _source(filters, scope="connector"):
    endpoint_ref = (
        {"scope": "connector", "connection_id": "conn_v1", "endpoint_id": "orders"}
        if scope == "connector"
        else {
            "scope": "connection",
            "connection_id": "conn_v1",
            "database_object": {"catalog": "c", "schema": "s", "name": "t"},
        }
    )
    return {"endpoint_ref": endpoint_ref, "filters": filters}


class TestNoDuplicateApiFilterLandings:
    def test_two_entries_same_field_and_operator_rejected(self):
        with pytest.raises(ValidationError, match=r"\[RULE-STRM-041\]"):
            StreamSource.model_validate(_source([
                {"field": "status", "operator": "neq", "value": "red"},
                {"field": "status", "operator": "neq", "value": "blue"},
            ]))

    def test_same_field_different_operator_accepted(self):
        StreamSource.model_validate(_source([
            {"field": "status", "operator": "neq", "value": "red"},
            {"field": "status", "operator": "eq", "value": "blue"},
        ]))

    def test_different_field_same_operator_accepted(self):
        StreamSource.model_validate(_source([
            {"field": "status", "operator": "neq", "value": "red"},
            {"field": "region", "operator": "neq", "value": "blue"},
        ]))

    def test_duplicate_pair_on_a_database_source_accepted(self):
        # A database source compiles each filter independently — no shared
        # landing to collide on, so the check is scoped off it entirely.
        StreamSource.model_validate(_source(
            [
                {"field": "status", "operator": "neq", "value": "red"},
                {"field": "status", "operator": "neq", "value": "blue"},
            ],
            scope="connection",
        ))
