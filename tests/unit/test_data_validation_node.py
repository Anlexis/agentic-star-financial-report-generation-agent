"""FIN-C2-007 — Unit tests: DataValidationNode.

Nodes are invoked through BaseNode.__call__ (`node(state)`) rather than
execute() directly, so the framework trust gate runs on every unit invocation.
DataValidationNode is an inner node, so it admits ANONYMOUS callers.
"""

import json

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.data_validation_node import DataValidationNode
from src.schemas.report_contract import MAX_PAYLOAD_ENTRIES

# Inner-node caller trust (ANONYMOUS) so BaseNode.__call__'s trust gate admits it.
_AN = TrustLevel.ANONYMOUS.value

# Payloads that satisfy every field contract, used as the base for mutation.
_VALID = {
    "risk_assessment": {
        "portfolio_id": "PF-001",
        "risk_score": 7.5,
        "total_exposure": 1500000,
        "currency": "JPY",
    },
    "credit_summary": {
        "client_id": "CL-0198",
        "credit_score": 720,
        "dti_ratio": 31.4,
        "credit_utilization": 42.0,
    },
    "portfolio_performance": {
        "portfolio_id": "PF-A1",
        "total_return": -3.25,
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
    },
    "compliance_status": {
        "entity_id": "ENT-A42",
        "reporting_period": "2025-Q4",
        "regulation_name": "basel_iii",
        "compliance_status": "COMPLIANT",
    },
}

# Every caller-controlled numeric field in the contract, by report type.
_NUMERIC_FIELDS = [
    ("risk_assessment", "risk_score"),
    ("risk_assessment", "total_exposure"),
    ("credit_summary", "credit_score"),
    ("credit_summary", "dti_ratio"),
    ("credit_summary", "credit_utilization"),
    ("portfolio_performance", "total_return"),
]

# Values that are not real, finite numbers. The string forms arrive through
# raw JSON — Python's json module parses bare NaN and Infinity — and the float
# forms arrive when a caller payload is built in process.
_NON_FINITE = [
    float("nan"),
    float("inf"),
    float("-inf"),
    "NaN",
    "Infinity",
    "-Infinity",
    True,
    "7.5",
    None,
    [1],
]


@pytest.fixture()
def node():
    return DataValidationNode()


def _state(report_type: str, data: dict) -> dict:
    return {
        "report_type": report_type,
        "input_data": json.dumps(data),
        "caller_trust_level": _AN,
    }


def _mutate(report_type: str, field: str, value) -> dict:
    payload = dict(_VALID[report_type])
    payload[field] = value
    return payload


class TestValidPayloads:
    """Each supported report type validates its own contract."""

    @pytest.mark.parametrize("report_type", sorted(_VALID))
    def test_valid_payload_passes(self, node, report_type):
        result = node(_state(report_type, _VALID[report_type]))
        assert result["status"] == AgentStatus.SUCCESS.value
        validated = json.loads(result["validated_data"])
        assert set(validated) == set(_VALID[report_type])

    def test_status_written_as_plain_string(self, node):
        result = node(_state("risk_assessment", _VALID["risk_assessment"]))
        # The exact type matters: the status enum is a str subclass, so an
        # isinstance() check would pass on a leaked enum member.
        assert type(result["status"]) is str  # noqa: E721

    def test_integer_values_stay_integers(self, node):
        """A whole-number amount must not be rewritten as a float."""
        result = node(_state("credit_summary", _VALID["credit_summary"]))
        validated = json.loads(result["validated_data"])
        assert validated["credit_score"] == 720
        assert isinstance(validated["credit_score"], int)

    def test_unknown_fields_are_dropped(self, node):
        payload = dict(_VALID["risk_assessment"])
        payload["injected_note"] = "should not survive"
        result = node(_state("risk_assessment", payload))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "injected_note" not in json.loads(result["validated_data"])

    @pytest.mark.parametrize("boundary", [0.0, 10.0])
    def test_range_boundaries_are_inclusive(self, node, boundary):
        result = node(_state("risk_assessment", _mutate("risk_assessment", "risk_score", boundary)))
        assert result["status"] == AgentStatus.SUCCESS.value


class TestNonFiniteNumbers:
    """Every caller number is finite and bounded, or the request fails closed.

    A NaN threshold compares False against everything, so a range check written
    as a bare comparison lets it through and it then suppresses the decision it
    takes part in. Each numeric field is probed with the whole matrix rather
    than trusting that one representative field covers the rest.
    """

    @pytest.mark.parametrize("report_type,field", _NUMERIC_FIELDS)
    @pytest.mark.parametrize("value", _NON_FINITE)
    def test_non_finite_value_is_rejected(self, node, report_type, field, value):
        result = node(_state(report_type, _mutate(report_type, field, value)))
        assert result["status"] == AgentStatus.ERROR.value
        assert any(field in message for message in result["error_log"])

    @pytest.mark.parametrize("report_type,field", _NUMERIC_FIELDS)
    def test_absurd_magnitude_is_rejected(self, node, report_type, field):
        """An unbounded amount must never reach the rendered document."""
        result = node(_state(report_type, _mutate(report_type, field, 10**60)))
        assert result["status"] == AgentStatus.ERROR.value

    def test_negative_amount_is_rejected(self, node):
        result = node(_state("risk_assessment", _mutate("risk_assessment", "total_exposure", -1)))
        assert result["status"] == AgentStatus.ERROR.value

    def test_rejected_value_is_not_echoed(self, node):
        """Error output names the field, never the value the caller sent."""
        secret = 987654321.5
        result = node(_state("risk_assessment", _mutate("risk_assessment", "risk_score", secret)))
        assert result["status"] == AgentStatus.ERROR.value
        joined = " ".join(result["error_log"])
        assert "risk_score" in joined
        assert "987654321" not in joined


class TestIdentifierContract:
    """Caller identifiers render into the report, so they stay inert."""

    @pytest.mark.parametrize("value", ["PF-2024-001", "PF_48210", "client_a", "ENT-A42", "a" * 32])
    def test_inert_identifier_accepted(self, node, value):
        result = node(_state("risk_assessment", _mutate("risk_assessment", "portfolio_id", value)))
        assert result["status"] == AgentStatus.SUCCESS.value

    @pytest.mark.parametrize(
        "value",
        [
            "PF 001",  # whitespace
            "PF|001",  # table structure
            "# Heading",  # document structure
            "<b>PF</b>",  # markup
            "${TOTAL_EXPOSURE}",  # a substitution marker
            "PF\n001",  # newline
            "a" * 33,  # over the length cap
            "",  # empty
            12345678,  # not a string
        ],
    )
    def test_non_inert_identifier_rejected(self, node, value):
        result = node(_state("risk_assessment", _mutate("risk_assessment", "portfolio_id", value)))
        assert result["status"] == AgentStatus.ERROR.value
        assert any("portfolio_id" in message for message in result["error_log"])

    def test_purely_numeric_identifier_rejected(self, node):
        """A digits-only reference is indistinguishable from a monetary figure.

        The output boundary snaps monetary-form runs onto the published grid.
        A reference such as "12345678" carries no neighbouring identifier
        character to protect it, so it would be rewritten in the caller's own
        report. It is refused at input instead.
        """
        result = node(_state("risk_assessment", _mutate("risk_assessment", "portfolio_id", "12345678")))
        assert result["status"] == AgentStatus.ERROR.value


class TestDatesAndEnums:
    @pytest.mark.parametrize("value", ["2024-13-01", "2024-02-30", "01-01-2024", "2024/01/01", "yesterday", 20240101])
    def test_invalid_date_rejected(self, node, value):
        result = node(_state("portfolio_performance", _mutate("portfolio_performance", "period_start", value)))
        assert result["status"] == AgentStatus.ERROR.value

    @pytest.mark.parametrize("value", ["JPY", "USD", "EUR", "GBP"])
    def test_supported_currency_accepted(self, node, value):
        result = node(_state("risk_assessment", _mutate("risk_assessment", "currency", value)))
        assert result["status"] == AgentStatus.SUCCESS.value

    @pytest.mark.parametrize("value", ["BTC", "jpy", "", None])
    def test_unsupported_currency_rejected(self, node, value):
        result = node(_state("risk_assessment", _mutate("risk_assessment", "currency", value)))
        assert result["status"] == AgentStatus.ERROR.value

    @pytest.mark.parametrize("value", ["COMPLIANT", "NON_COMPLIANT", "UNDER_REVIEW"])
    def test_compliance_status_values(self, node, value):
        result = node(_state("compliance_status", _mutate("compliance_status", "compliance_status", value)))
        assert result["status"] == AgentStatus.SUCCESS.value

    def test_unknown_compliance_status_rejected(self, node):
        result = node(_state("compliance_status", _mutate("compliance_status", "compliance_status", "MAYBE")))
        assert result["status"] == AgentStatus.ERROR.value


class TestStructuralLimits:
    def test_missing_required_field(self, node):
        payload = dict(_VALID["risk_assessment"])
        del payload["portfolio_id"]
        result = node(_state("risk_assessment", payload))
        assert result["status"] == AgentStatus.ERROR.value
        assert any("portfolio_id" in message for message in result["error_log"])

    def test_entry_cap_enforced(self, node):
        payload = dict(_VALID["risk_assessment"])
        payload.update({f"filler_{index}": index for index in range(MAX_PAYLOAD_ENTRIES)})
        result = node(_state("risk_assessment", payload))
        assert result["status"] == AgentStatus.ERROR.value

    def test_missing_report_type(self, node):
        result = node({"input_data": "{}", "caller_trust_level": _AN})
        assert result["status"] == AgentStatus.ERROR.value

    def test_missing_input_data(self, node):
        result = node({"report_type": "risk_assessment", "caller_trust_level": _AN})
        assert result["status"] == AgentStatus.ERROR.value

    def test_non_json_input_data(self, node):
        result = node({"report_type": "risk_assessment", "input_data": "not json", "caller_trust_level": _AN})
        assert result["status"] == AgentStatus.ERROR.value

    def test_input_data_must_be_an_object(self, node):
        result = node({"report_type": "risk_assessment", "input_data": "[1, 2]", "caller_trust_level": _AN})
        assert result["status"] == AgentStatus.ERROR.value

    def test_unknown_report_type(self, node):
        result = node(_state("weather_forecast", {"anything": 1}))
        assert result["status"] == AgentStatus.ERROR.value

    def test_required_trust_level(self, node):
        assert node.required_trust_level is TrustLevel.ANONYMOUS
