"""AgentCore Platform v1.0"""

# FIN-C2-007 — ReportGenerationNode: the agent's main processing step.
#
# The agent backbone offers one domain slot between the input boundary and the
# output boundary, and generating a financial report takes three steps:
#
#     validate the caller's data  ->  select the document template
#                                 ->  render the document
#
# This node owns that sequence.  Each step is an independent node with its own
# contract and its own tests; this node runs them in order, threading each
# step's partial state update into the next step's view of state, and stops at
# the first step that reports an error so a failed validation never reaches
# the renderer.
#
# The state updates the steps produce are returned as one partial dict, so the
# node behaves like any other single node from the backbone's point of view.
#
# Audit: emit_trace_event() when the pipeline completes.

from typing import Any, ClassVar, Dict, List, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.nodes.data_validation_node import DataValidationNode
from src.nodes.report_draft_node import ReportDraftNode
from src.nodes.template_selector_node import TemplateSelectorNode


class ReportGenerationNode(FunctionNode):
    """Run validation, template selection and rendering as one backbone step.

    Input state keys:  report_type, input_data
    Output state keys: validated_data, selected_template, report_draft, status
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self) -> None:
        super().__init__()
        self._steps: Tuple[FunctionNode, ...] = (
            DataValidationNode(),
            TemplateSelectorNode(),
            ReportDraftNode(),
        )

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # A reason settled earlier in the run is the real one: pass it through
        # untouched instead of doing work on input that was already declined.
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        working: Dict[str, Any] = dict(state)
        produced: Dict[str, Any] = {}
        completed: List[str] = []

        for step in self._steps:
            update: Dict[str, Any] = step.execute(working, config)
            if update.get("status") == AgentStatus.ERROR.value:
                emit_trace_event(
                    "financial_report_pipeline_failed",
                    {"failed_step": type(step).__name__, "completed_steps": completed},
                    state,
                )
                return update
            working.update(update)
            produced.update(update)
            completed.append(type(step).__name__)

        emit_trace_event(
            "financial_report_pipeline_completed",
            {"completed_steps": completed},
            state,
        )

        produced["status"] = AgentStatus.SUCCESS.value
        return produced
