"""FIN-C2-007 — Unit tests: ReportDraftNode.

Nodes are invoked through BaseNode.__call__ (`node(state)`) rather than
execute() directly, so the framework trust gate runs on every unit invocation.
ReportDraftNode is an inner node, so it admits ANONYMOUS callers.
"""

import json

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.report_draft_node import PRECISION_NOTE, ReportDraftNode
from src.schemas.report_contract import EXTERNAL_ROUND_UNIT

# Inner-node caller trust (ANONYMOUS) so BaseNode.__call__'s trust gate admits it.
_AN = TrustLevel.ANONYMOUS.value

_SIMPLE_TEMPLATE = "Portfolio: ${PORTFOLIO_ID}\nRisk Score: ${RISK_SCORE}\n"


@pytest.fixture()
def node():
    return ReportDraftNode()


def _state(data: dict, template: str) -> dict:
    return {
        "validated_data": json.dumps(data),
        "selected_template": template,
        "report_type": "risk_assessment",
        "caller_trust_level": _AN,
    }


class TestSubstitution:
    def test_markers_are_replaced(self, node):
        result = node(_state({"portfolio_id": "PF-001", "risk_score": 7.5}, _SIMPLE_TEMPLATE))
        assert result["status"] == AgentStatus.SUCCESS.value
        draft = result["report_draft"]
        assert "PF-001" in draft
        assert "7.5" in draft
        assert "${" not in draft

    def test_unknown_marker_becomes_a_visible_gap(self, node):
        template = "Score: ${RISK_SCORE}\nOther: ${UNKNOWN_FIELD}\n"
        result = node(_state({"risk_score": 7.5}, template))
        assert "[TBD]" in result["report_draft"]
        assert "${UNKNOWN_FIELD}" not in result["report_draft"]

    def test_narrative_section_is_filled(self, node):
        template = "Analysis: ${RISK_ANALYSIS_NARRATIVE}\n"
        result = node(_state({"risk_score": 7.5}, template))
        assert "${RISK_ANALYSIS_NARRATIVE}" not in result["report_draft"]
        assert "[TBD]" not in result["report_draft"]

    def test_empty_template_yields_an_empty_draft(self, node):
        result = node(_state({"risk_score": 7.5}, ""))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["report_draft"] == ""

    def test_status_written_as_plain_string(self, node):
        result = node(_state({"portfolio_id": "PF-001"}, _SIMPLE_TEMPLATE))
        # The exact type matters: the status enum is a str subclass, so an
        # isinstance() check would pass on a leaked enum member.
        assert type(result["status"]) is str  # noqa: E721


class TestPublishedPrecision:
    """Monetary amounts render on the published grid, never at line precision."""

    _TEMPLATE = "Exposure: ${CURRENCY} ${TOTAL_EXPOSURE}\n"

    @pytest.mark.parametrize(
        "amount,expected",
        [
            (1234567, "1,235,000"),
            (1500000, "1,500,000"),
            (999, "1,000"),
            (1499, "1,000"),
            (0, "0"),
        ],
    )
    def test_amount_renders_on_the_grid(self, node, amount, expected):
        result = node(_state({"currency": "JPY", "total_exposure": amount}, self._TEMPLATE))
        assert f"JPY {expected}" in result["report_draft"]
        assert str(amount) not in result["report_draft"].replace(expected, "")

    def test_report_states_its_precision(self, node):
        result = node(_state({"currency": "JPY", "total_exposure": 1234567}, self._TEMPLATE))
        assert PRECISION_NOTE in result["report_draft"]
        assert f"{EXTERNAL_ROUND_UNIT:,d}" in PRECISION_NOTE

    def test_no_precision_note_without_monetary_fields(self, node):
        result = node(_state({"portfolio_id": "PF-001", "risk_score": 7.5}, _SIMPLE_TEMPLATE))
        assert PRECISION_NOTE not in result["report_draft"]

    def test_non_monetary_numbers_render_unchanged(self, node):
        template = "Score: ${CREDIT_SCORE}\nRatio: ${DTI_RATIO}%\n"
        result = node(_state({"credit_score": 720, "dti_ratio": 31.4}, template))
        assert "720" in result["report_draft"]
        assert "31.4" in result["report_draft"]


class TestRejectedDrafts:
    def test_missing_validated_data(self, node):
        result = node({"selected_template": _SIMPLE_TEMPLATE, "caller_trust_level": _AN})
        assert result["status"] == AgentStatus.ERROR.value

    def test_missing_template(self, node):
        result = node({"validated_data": "{}", "caller_trust_level": _AN})
        assert result["status"] == AgentStatus.ERROR.value

    def test_non_json_validated_data(self, node):
        result = node(
            {
                "validated_data": "not json",
                "selected_template": _SIMPLE_TEMPLATE,
                "caller_trust_level": _AN,
            }
        )
        assert result["status"] == AgentStatus.ERROR.value


class TestNodeShape:
    def test_required_trust_level(self, node):
        assert node.required_trust_level is TrustLevel.ANONYMOUS

    def test_execute_signature(self, node):
        import inspect

        parameters = list(inspect.signature(node.execute).parameters)
        assert parameters[0] == "state"
        assert not hasattr(node, "_invoke_impl")
