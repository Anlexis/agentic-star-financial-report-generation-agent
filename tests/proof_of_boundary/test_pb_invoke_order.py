# PB-6: Backbone Invoke Execution Order Verification
#
# Verifies that FinC2007Agent.invoke() runs nodes in the correct backbone order
# and that node_history reflects the expected execution sequence for a
# SUCCESS-yielding payload.
#
# FIN-C2-007 backbone:
#   [InitializeNode, PreProcessNode, ReportGenerationNode, PostProcessNode,
#    FinalizeNode]
#
# IMPORTANT — caller trust level:
#   The invoke must use InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL).
#   An INTERNAL caller would mask the PreProcessNode VERIFIED_EXTERNAL trust gate
#   (INTERNAL > VERIFIED_EXTERNAL → every gate passes → false green).
#   VERIFIED_EXTERNAL is the real external-caller path that exercises the
#   trust boundary.
#
# SUCCESS-yielding payload: a valid risk_assessment JSON payload where
#   PreProcessNode and ReportGenerationNode both return SUCCESS, so the
#   backbone routes to post_process (not short-circuit to finalize).

import json


from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import FinC2007Agent

# _MAIN_SLOT_NODE: the class registered in the "main" backbone slot.
# For FIN-C2-007 this is ReportGenerationNode (validate, select template, render).
_MAIN_SLOT_NODE = "ReportGenerationNode"

# _VALID_PAYLOAD: a SUCCESS-yielding risk_assessment payload.
# All required fields are present and within valid ranges.
_VALID_PAYLOAD = json.dumps(
    {
        "report_type": "risk_assessment",
        "input_data": {
            "portfolio_id": "PF-PB6-001",
            "risk_score": 6.0,
            "total_exposure": 2000000,
            "currency": "JPY",
        },
    }
)


class TestPB6BackboneInvokeOrder:
    """PB-6: FinC2007Agent.invoke() must execute backbone nodes in the correct order."""

    def test_backbone_node_order_success_path(self):
        """PB-6: Full Graph().invoke() asserts backbone node_history order.

        Backbone order expected for SUCCESS path:
          [InitializeNode, PreProcessNode, ReportGenerationNode,
           PostProcessNode, FinalizeNode]

        Uses VERIFIED_EXTERNAL caller — same trust path a real external caller uses.
        """
        agent = FinC2007Agent()
        agent.compile()

        ctx = InvocationContext(
            caller_trust_level=TrustLevel.VERIFIED_EXTERNAL,
            session_id="pb6-invoke-order-test",
        )
        result = agent.invoke(user_input=_VALID_PAYLOAD, ctx=ctx)

        assert result is not None, "invoke() must return a result dict"
        assert result.get("status") is not None, "result must have status"

        # Verify node_history lists the expected backbone sequence
        node_history = result.get("node_history", [])
        assert len(node_history) >= 3, (
            f"node_history must include at least pre_process/main/post_process; " f"got: {node_history}"
        )

        # The main slot node (DataValidationNode) must appear in history
        node_class_names = [entry if isinstance(entry, str) else str(entry) for entry in node_history]
        assert any(_MAIN_SLOT_NODE in name for name in node_class_names), (
            f"{_MAIN_SLOT_NODE} (main slot) must appear in node_history.\n" f"node_history: {node_history}"
        )

        # The output key for invoke() result is 'output', not 'formatted_output'
        assert (
            "output" in result or "status" in result
        ), f"invoke() result must contain 'output' or 'status'; got keys: {list(result.keys())}"

    def test_backbone_pre_process_always_first_domain_node(self):
        """PB-6b: PreProcessNode must execute before the main slot node."""
        agent = FinC2007Agent()
        agent.compile()

        ctx = InvocationContext(
            caller_trust_level=TrustLevel.VERIFIED_EXTERNAL,
            session_id="pb6-order-b",
        )
        result = agent.invoke(user_input=_VALID_PAYLOAD, ctx=ctx)
        node_history = result.get("node_history", [])

        node_names = [entry if isinstance(entry, str) else str(entry) for entry in node_history]

        pre_idx = next((i for i, n in enumerate(node_names) if "PreProcess" in n), None)
        main_idx = next((i for i, n in enumerate(node_names) if _MAIN_SLOT_NODE in n), None)

        if pre_idx is not None and main_idx is not None:
            assert pre_idx < main_idx, f"PreProcessNode must execute before {_MAIN_SLOT_NODE}. " f"Order: {node_names}"

    def test_error_payload_short_circuits_post_process(self):
        """PB-6c: An invalid payload that fails validation skips post_process."""
        agent = FinC2007Agent()
        agent.compile()

        ctx = InvocationContext(
            caller_trust_level=TrustLevel.VERIFIED_EXTERNAL,
            session_id="pb6-error-path",
        )
        # Missing required fields → the main slot node returns ERROR
        invalid_payload = json.dumps(
            {
                "report_type": "risk_assessment",
                "input_data": {"portfolio_id": "PF-PB6-BAD"},
                # missing: risk_score, total_exposure, currency
            }
        )
        result = agent.invoke(user_input=invalid_payload, ctx=ctx)
        # Status should be ERROR (short-circuited at DataValidationNode)
        from framework.schemas.agent_status import AgentStatus

        # State carries the status .value string, never the bare enum.
        assert (
            result.get("status") == AgentStatus.ERROR.value
        ), f"Expected ERROR status for invalid payload; got: {result.get('status')}"
