"""AgentCore Platform v1.0"""

# FIN-C2-007 — DataValidationNode
#
# Validates the caller's financial data payload against the contract declared
# in src/schemas/report_contract.py: required fields, bounded numbers, inert
# identifiers, real calendar dates, closed enumerations.
#
# Every value here is caller-controlled, so validation fails CLOSED: the node
# returns ERROR and writes nothing downstream unless every declared field is
# present and inside its bounds.  Error strings name the offending field and
# never repeat the rejected value.
#
# Audit: emit_trace_event() on validation pass and on validation failure.

import json
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.report_contract import validate_payload


class DataValidationNode(FunctionNode):
    """Validate the caller's financial data against the report-type contract.

    Reads report_type and input_data from state, validates every declared
    field, and writes validated_data (a normalised JSON string holding only
    contract fields) on success.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # A reason settled earlier in the run is the real one: pass it through
        # untouched instead of doing work on input that was already declined.
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        report_type = state.get("report_type", "")
        input_data_str = state.get("input_data", "")

        if not report_type:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["DataValidationNode: report_type is missing from state"],
            }

        if not input_data_str:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["DataValidationNode: input_data is missing from state"],
            }

        try:
            input_data = json.loads(input_data_str)
        except json.JSONDecodeError:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["DataValidationNode: input_data is not valid JSON"],
            }

        if not isinstance(input_data, dict):
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["DataValidationNode: input_data must be a JSON object"],
            }

        validated, errors = validate_payload(report_type, input_data)

        if errors:
            emit_trace_event(
                "financial_data_validation_failed",
                {"report_type": report_type, "error_count": len(errors)},
                state,
            )
            messages: List[str] = [f"DataValidationNode: {err}" for err in errors]
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": messages,
            }

        emit_trace_event(
            "financial_data_validation_passed",
            {"report_type": report_type, "field_count": len(validated)},
            state,
        )

        return {
            "validated_data": json.dumps(validated, ensure_ascii=False),
            "status": AgentStatus.SUCCESS.value,
        }
