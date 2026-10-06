"""FIN-C2-007 — Unit tests: ReportGenerationNode, the agent's main step.

Nodes are invoked through BaseNode.__call__ (`node(state)`) rather than
execute() directly, so the framework trust gate runs on every unit invocation.
ReportGenerationNode is a backbone inner node, so it admits ANONYMOUS callers.
"""

import json

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.report_generation_node import ReportGenerationNode

# Inner-node caller trust (ANONYMOUS) so BaseNode.__call__'s trust gate admits it.
_AN = TrustLevel.ANONYMOUS.value

_RISK_DATA = {
    "portfolio_id": "PF-001",
    "risk_score": 7.5,
    "total_exposure": 1234567,
    "currency": "JPY",
}


@pytest.fixture()
def node():
    return ReportGenerationNode()


def _state(report_type: str, data: dict) -> dict:
    return {
        "report_type": report_type,
        "input_data": json.dumps(data),
        "caller_trust_level": _AN,
    }


class TestPipeline:
    def test_all_three_steps_produce_a_document(self, node):
        result = node(_state("risk_assessment", _RISK_DATA))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "validated_data" in result
        assert "selected_template" in result
        assert "report_draft" in result
        assert "PF-001" in result["report_draft"]

    def test_amount_is_rendered_on_the_published_grid(self, node):
        result = node(_state("risk_assessment", _RISK_DATA))
        assert "JPY 1,235,000" in result["report_draft"]
        assert "1234567" not in result["report_draft"]

    @pytest.mark.parametrize(
        "report_type,data",
        [
            (
                "credit_summary",
                {"client_id": "CL-1", "credit_score": 720, "dti_ratio": 31.4, "credit_utilization": 42.0},
            ),
            (
                "portfolio_performance",
                {
                    "portfolio_id": "PF-1",
                    "total_return": -3.25,
                    "period_start": "2024-01-01",
                    "period_end": "2024-12-31",
                },
            ),
            (
                "compliance_status",
                {
                    "entity_id": "ENT-A1",
                    "reporting_period": "2025-Q4",
                    "regulation_name": "basel_iii",
                    "compliance_status": "COMPLIANT",
                },
            ),
        ],
    )
    def test_every_report_type_produces_a_document(self, node, report_type, data):
        result = node(_state(report_type, data))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["report_draft"].strip()

    def test_status_written_as_plain_string(self, node):
        result = node(_state("risk_assessment", _RISK_DATA))
        # The exact type matters: the status enum is a str subclass, so an
        # isinstance() check would pass on a leaked enum member.
        assert type(result["status"]) is str  # noqa: E721


class TestShortCircuit:
    def test_validation_failure_stops_before_rendering(self, node):
        """A rejected payload must never reach the renderer."""
        result = node(_state("risk_assessment", dict(_RISK_DATA, risk_score=float("nan"))))
        assert result["status"] == AgentStatus.ERROR.value
        assert "report_draft" not in result
        assert "validated_data" not in result

    def test_missing_report_type_stops_at_the_first_step(self, node):
        result = node({"input_data": "{}", "caller_trust_level": _AN})
        assert result["status"] == AgentStatus.ERROR.value
        assert "report_draft" not in result


class TestNodeShape:
    def test_required_trust_level(self, node):
        assert node.required_trust_level is TrustLevel.ANONYMOUS

    def test_execute_signature(self, node):
        import inspect

        parameters = list(inspect.signature(node.execute).parameters)
        assert parameters[0] == "state"
        assert not hasattr(node, "_invoke_impl")
