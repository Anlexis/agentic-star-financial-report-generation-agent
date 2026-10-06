"""FIN-C2-007 — Unit tests: TemplateSelectorNode.

Nodes are invoked through BaseNode.__call__ (`node(state)`) rather than
execute() directly, so the framework trust gate runs on every unit invocation.
TemplateSelectorNode is an inner node, so it admits ANONYMOUS callers.
"""

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.template_selector_node import TemplateSelectorNode
from src.schemas.report_contract import SUPPORTED_REPORT_TYPES

# Inner-node caller trust (ANONYMOUS) so BaseNode.__call__'s trust gate admits it.
_AN = TrustLevel.ANONYMOUS.value


@pytest.fixture()
def node():
    return TemplateSelectorNode()


def _state(report_type) -> dict:
    return {"report_type": report_type, "caller_trust_level": _AN}


class TestTemplateSelection:
    @pytest.mark.parametrize("report_type", sorted(SUPPORTED_REPORT_TYPES))
    def test_every_report_type_has_a_template(self, node, report_type):
        result = node(_state(report_type))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "${" in result["selected_template"]

    def test_risk_assessment_template_content(self, node):
        result = node(_state("risk_assessment"))
        template = result["selected_template"]
        assert "Risk Assessment" in template
        assert "${PORTFOLIO_ID}" in template

    def test_status_written_as_plain_string(self, node):
        result = node(_state("risk_assessment"))
        # The exact type matters: the status enum is a str subclass, so an
        # isinstance() check would pass on a leaked enum member.
        assert type(result["status"]) is str  # noqa: E721


class TestRejectedSelection:
    @pytest.mark.parametrize("report_type", ["", None, 0])
    def test_missing_report_type(self, node, report_type):
        result = node(_state(report_type))
        assert result["status"] == AgentStatus.ERROR.value

    @pytest.mark.parametrize(
        "report_type",
        ["weather_forecast", "../../etc/passwd", "risk_assessment/../secrets"],
    )
    def test_unsupported_report_type_never_reaches_the_filesystem(self, node, report_type):
        """report_type is used to build a path, so the node re-checks it itself."""
        result = node(_state(report_type))
        assert result["status"] == AgentStatus.ERROR.value
        assert "selected_template" not in result


class TestNodeShape:
    def test_required_trust_level(self, node):
        assert node.required_trust_level is TrustLevel.ANONYMOUS

    def test_execute_signature(self, node):
        import inspect

        parameters = list(inspect.signature(node.execute).parameters)
        assert parameters[0] == "state"
        assert not hasattr(node, "_invoke_impl")
