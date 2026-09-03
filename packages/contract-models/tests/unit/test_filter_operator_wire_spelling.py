"""An operator's comparison is written to a param nothing else writes.

A request binding maps a wire name to one value. So the param a filter operator
names is what makes two comparisons distinguishable on the wire: `gt` and `lt`
writing one param describe one request, and neither document then records which
of the two was asked for. The endpoint declares, per filterable record field,
which param carries each operator — and, where a provider spells the comparison
inside the value rather than the key, the template that value is spelled with.
"""
import pytest
from pydantic import ValidationError

from analitiq.contracts.endpoints import Param, parse_endpoint

API_SCHEMA_URL = "https://schemas.analitiq.ai/api-endpoint/latest.json"
JSON_SCHEMA = "https://json-schema.org/draft/2020-12/schema"

#: Record fields the fixtures below may filter on.
_RECORD_FIELDS = ("amount", "created")


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
                        "items": {
                            "type": "object",
                            "properties": {f: {"type": "string"} for f in _RECORD_FIELDS},
                        },
                    },
                },
            },
        },
    }


_NUM = {"in": "query", "type": "number", "required": False}


def _params(*names):
    return {n: dict(_NUM) for n in names}


def _query(*names):
    return {n: {"from_param": n} for n in names}


class TestFilterabilityIsNotAParamDeclaration:
    def test_a_param_does_not_declare_filter_operators(self):
        """Filterability is the read operation's to declare, not the param's."""
        with pytest.raises(ValidationError, match="operators"):
            Param.model_validate(
                {"in": "query", "type": "number", "required": False, "operators": ["gt", "lt"]}
            )


class TestOneParamCarriesOneComparison:
    def test_two_operators_may_name_two_params(self):
        doc = parse_endpoint(
            _doc(
                _params("minAmount", "maxAmount"),
                {"amount": {"gt": {"from_param": "minAmount"},
                            "lt": {"from_param": "maxAmount"}}},
                _query("minAmount", "maxAmount"),
            )
        )
        assert doc.operations.read.filters["amount"]["gt"].from_param == "minAmount"

    def test_two_operators_on_one_field_may_not_name_one_param(self):
        with pytest.raises(ValidationError, match="carries one comparison"):
            parse_endpoint(
                _doc(
                    _params("amount"),
                    {"amount": {"gt": {"from_param": "amount"},
                                "lt": {"from_param": "amount"}}},
                    _query("amount"),
                )
            )

    def test_two_record_fields_may_not_name_one_param(self):
        """The param is the site, so the collision is operation-wide, not per field."""
        with pytest.raises(ValidationError, match="carries one comparison"):
            parse_endpoint(
                _doc(
                    _params("q"),
                    {"amount": {"gt": {"from_param": "q"}},
                     "created": {"gt": {"from_param": "q"}}},
                    _query("q"),
                )
            )

    def test_a_template_does_not_exempt_a_param_from_the_collision(self):
        with pytest.raises(ValidationError, match="carries one comparison"):
            parse_endpoint(
                _doc(
                    _params("q"),
                    {"amount": {"gt": {"from_param": "q"},
                                "lt": {"from_param": "q",
                                       "template": "<${stream.filter.value}"}}},
                    _query("q"),
                )
            )


class TestTheParamMustBeOneAStreamMayWrite:
    def test_an_unknown_param_is_refused(self):
        with pytest.raises(ValidationError, match="unknown param"):
            parse_endpoint(
                _doc(_params("amount"),
                     {"amount": {"gt": {"from_param": "nope"}}},
                     _query("amount"))
            )

    def test_a_runtime_owned_param_is_refused(self):
        with pytest.raises(ValidationError, match="controlled_by"):
            parse_endpoint(
                _doc(
                    {"page": {"in": "query", "type": "integer", "required": False,
                              "controlled_by": "pagination"}},
                    {"amount": {"gt": {"from_param": "page"}}},
                    _query("page"),
                )
            )

    def test_a_landing_site_must_name_a_param(self):
        """A template alone names no wire slot, so it names no landing site."""
        with pytest.raises(ValidationError, match="from_param"):
            parse_endpoint(
                _doc(_params("q"),
                     {"amount": {"gt": {"template": ">${stream.filter.value}"}}},
                     _query("q"))
            )

    def test_an_unknown_sibling_key_is_refused(self):
        with pytest.raises(ValidationError, match="rogue"):
            parse_endpoint(
                _doc(_params("q"),
                     {"amount": {"gt": {"from_param": "q", "rogue": 1}}},
                     _query("q"))
            )


class TestTheTemplateCarriesTheValue:
    def test_a_template_spells_the_comparison_into_the_value(self):
        """`amount=<>0` — the provider writes the comparison inside the value."""
        doc = parse_endpoint(
            _doc(_params("amount"),
                 {"amount": {"neq": {"from_param": "amount",
                                     "template": "<>${stream.filter.value}"}}},
                 _query("amount"))
        )
        assert doc.operations.read.filters["amount"]["neq"].template == (
            "<>${stream.filter.value}"
        )

    def test_a_template_dropping_the_filter_value_is_refused(self):
        with pytest.raises(ValidationError, match="stream.filter.value"):
            parse_endpoint(
                _doc(_params("amount"),
                     {"amount": {"gt": {"from_param": "amount", "template": ">100"}}},
                     _query("amount"))
            )

    def test_an_unknown_scope_in_a_template_is_refused(self):
        with pytest.raises(ValidationError, match="resolution scope"):
            parse_endpoint(
                _doc(_params("amount"),
                     {"amount": {"gt": {"from_param": "amount",
                                        "template": ">${nonsense.value}"}}},
                     _query("amount"))
            )


class TestTheFieldMustBeOneTheRecordsCarry:
    def test_a_field_the_record_shape_does_not_declare_is_refused(self):
        with pytest.raises(ValidationError, match="nosuchfield"):
            parse_endpoint(
                _doc(_params("q"),
                     {"nosuchfield": {"gt": {"from_param": "q"}}},
                     _query("q"))
            )

    def test_a_field_offering_no_operator_is_refused(self):
        with pytest.raises(ValidationError, match="offers no operator"):
            parse_endpoint(
                _doc(_params("q"), {"amount": {}}, _query("q"))
            )

    def test_an_operator_outside_the_api_vocabulary_is_refused(self):
        """`is_null` is a dialect spelling; a provider cannot be asked for it."""
        with pytest.raises(ValidationError, match="is_null"):
            parse_endpoint(
                _doc(_params("q"),
                     {"amount": {"is_null": {"from_param": "q"}}},
                     _query("q"))
            )
