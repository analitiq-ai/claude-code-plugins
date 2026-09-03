"""An operator reaches the wire through a landing site the endpoint names.

The defect these tests pin: a param used to declare a *list* of operators, which
said which comparisons a stream filter could ask for and nothing at all about how
any of them was spelled on the request. Two operators on one param therefore built
one byte-identical request, and the read returned the wrong rows with no warning.

The endpoint now declares, per filterable record field, where each operator lands:
a param of its own (`minAmount`/`maxAmount`, `amount__gt`), or a rendering of the
value itself (`amount=<>0`, `$filter=amount gt 100`). Two operators that resolve to
one landing site are refused, so the identical-request case cannot be authored.
"""
import pytest
from pydantic import ValidationError

from analitiq.contracts.endpoints import Param, parse_endpoint

API_SCHEMA_URL = "https://schemas.analitiq.ai/api-endpoint/latest.json"
JSON_SCHEMA = "https://json-schema.org/draft/2020-12/schema"


def _doc(params, filters, query):
    return {
        "$schema": API_SCHEMA_URL,
        "endpoint_id": "records",
        "operations": {
            "read": {
                "request": {"method": "GET", "path": "/v1/records", "query": query},
                "params": params,
                "filters": filters,
                "response": {
                    "records": {"ref": "response.body"},
                    "schema": {
                        "$schema": JSON_SCHEMA,
                        "type": "array",
                        "items": {"type": "object"},
                    },
                },
            },
        },
    }


_NUM = {"in": "query", "type": "number", "required": False}


class TestParamOperatorsIsGone:
    def test_param_no_longer_declares_an_operator_list(self):
        """The field that declared a vocabulary and no spelling is removed."""
        with pytest.raises(ValidationError):
            Param.model_validate(
                {"in": "query", "type": "number", "required": False, "operators": ["gt", "lt"]}
            )


class TestOperatorLandsInAParam:
    def test_two_operators_may_name_two_params(self):
        doc = parse_endpoint(
            _doc(
                params={"minAmount": dict(_NUM), "maxAmount": dict(_NUM)},
                filters={
                    "amount": {
                        "gt": {"from_param": "minAmount"},
                        "lt": {"from_param": "maxAmount"},
                    }
                },
                query={
                    "minAmount": {"from_param": "minAmount"},
                    "maxAmount": {"from_param": "maxAmount"},
                },
            )
        )
        assert doc.operations.read.filters["amount"]["gt"] == {"from_param": "minAmount"}

    def test_two_operators_may_not_name_one_param(self):
        """The wrong-rows case: one landing site cannot carry two comparisons."""
        with pytest.raises(ValidationError, match="same landing site"):
            parse_endpoint(
                _doc(
                    params={"amount": dict(_NUM)},
                    filters={
                        "amount": {
                            "gt": {"from_param": "amount"},
                            "lt": {"from_param": "amount"},
                        }
                    },
                    query={"amount": {"from_param": "amount"}},
                )
            )

    def test_operator_naming_an_undeclared_param_is_refused(self):
        with pytest.raises(ValidationError, match="unknown param"):
            parse_endpoint(
                _doc(
                    params={"amount": dict(_NUM)},
                    filters={"amount": {"gt": {"from_param": "nope"}}},
                    query={"amount": {"from_param": "amount"}},
                )
            )

    def test_operator_may_not_name_a_runtime_owned_param(self):
        with pytest.raises(ValidationError, match="controlled_by"):
            parse_endpoint(
                _doc(
                    params={
                        "page": {
                            "in": "query",
                            "type": "integer",
                            "required": False,
                            "controlled_by": "pagination",
                        }
                    },
                    filters={"amount": {"gt": {"from_param": "page"}}},
                    query={"page": {"from_param": "page"}},
                )
            )


class TestOperatorLandsInTheValue:
    def test_a_template_may_render_the_comparison_into_the_value(self):
        """`amount=<>0` — the provider spells the comparison inside the value."""
        doc = parse_endpoint(
            _doc(
                params={"amount": dict(_NUM)},
                filters={
                    "amount": {
                        "gt": {"template": ">${stream.filter.value}"},
                        "neq": {"template": "<>${stream.filter.value}"},
                    }
                },
                query={"amount": {"from_param": "amount"}},
            )
        )
        assert doc.operations.read.filters["amount"]["neq"] == {
            "template": "<>${stream.filter.value}"
        }

    def test_a_template_that_drops_the_filter_value_is_refused(self):
        """A comparison that never interpolates the value filters on nothing."""
        with pytest.raises(ValidationError, match="stream.filter.value"):
            parse_endpoint(
                _doc(
                    params={"amount": dict(_NUM)},
                    filters={"amount": {"gt": {"template": ">100"}}},
                    query={"amount": {"from_param": "amount"}},
                )
            )

    def test_two_operators_may_not_share_one_template(self):
        with pytest.raises(ValidationError, match="same landing site"):
            parse_endpoint(
                _doc(
                    params={"amount": dict(_NUM)},
                    filters={
                        "amount": {
                            "gt": {"template": ">${stream.filter.value}"},
                            "gte": {"template": ">${stream.filter.value}"},
                        }
                    },
                    query={"amount": {"from_param": "amount"}},
                )
            )

    def test_an_unknown_scope_in_a_template_is_refused(self):
        with pytest.raises(ValidationError, match="scope"):
            parse_endpoint(
                _doc(
                    params={"amount": dict(_NUM)},
                    filters={"amount": {"gt": {"template": ">${nonsense.value}"}}},
                    query={"amount": {"from_param": "amount"}},
                )
            )


class TestLandingSiteShape:
    def test_a_binding_declares_exactly_one_landing_site(self):
        with pytest.raises(ValidationError):
            parse_endpoint(
                _doc(
                    params={"amount": dict(_NUM)},
                    filters={
                        "amount": {
                            "gt": {
                                "from_param": "amount",
                                "template": ">${stream.filter.value}",
                            }
                        }
                    },
                    query={"amount": {"from_param": "amount"}},
                )
            )

    def test_an_operator_outside_the_api_vocabulary_is_refused(self):
        with pytest.raises(ValidationError):
            parse_endpoint(
                _doc(
                    params={"amount": dict(_NUM)},
                    filters={"amount": {"is_null": {"from_param": "amount"}}},
                    query={"amount": {"from_param": "amount"}},
                )
            )
