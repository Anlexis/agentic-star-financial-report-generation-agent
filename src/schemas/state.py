"""AgentCore Platform v1.0"""

# State must be a flat TypedDict — never a Pydantic model.  Graph
# checkpoints are serialized with msgpack, which corrupts rich objects
# silently.  Extend AgentState with agent-specific fields only, and never
# add credentials, secrets or model instances.
#
# For the same reason, structured fields (dict / list) are stored as JSON
# STRINGS rather than bare Python containers: producers serialize on write,
# consumers deserialize on read, via to_json() / from_json().
#
# FIN-C2-007 — Financial Report Generation Agent
# Pipeline: PreProcess -> ReportGeneration (validate, select, render)
#           -> PostProcess

import json
from typing import Any, Optional

from framework.schemas.agent_state import AgentState


def to_json(value: Any) -> Optional[str]:
    """Serialize a dict/list State field to a JSON string (checkpoint safety)."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def from_json(value: Optional[str], default: Any = None) -> Any:
    """Deserialize a JSON-string State field back to its dict/list."""
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


class State(AgentState):
    """Flat TypedDict for FIN-C2-007 Financial Report Generation Agent.

    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, etc.) are inherited from AgentState.

    Field safety notes:
    - No JWT, API keys, credentials, or Pydantic objects stored here.
    - Structured data (input_data) is stored as a JSON string.
    - Financial metrics are stored as strings/numbers — no credential-like names.
    """

    # PreProcessNode output — sanitized user_input string
    validated_input: Optional[str]

    # PreProcessNode output — the requested report type
    # Values: "risk_assessment" | "credit_summary" | "portfolio_performance"
    #         | "compliance_status"
    report_type: Optional[str]

    # PreProcessNode output — JSON STRING of the caller's financial data.
    # Financial metrics only; never credentials.
    # Producers: to_json(input_dict); Consumers: from_json(input_data)
    input_data: Optional[str]

    # DataValidationNode output — JSON STRING of validated data: only the
    # fields the report contract declares, each checked against its bounds.
    validated_data: Optional[str]

    # TemplateSelectorNode output — selected document template content string
    # Contains ${FIELD_NAME} markers for ReportDraftNode substitution.
    selected_template: Optional[str]

    # ReportDraftNode output — generated report text after template substitution
    report_draft: Optional[str]

    # PostProcessNode output — MUST be declared for LangGraph TypedDict merge.
    # AgentBaseGraph.get_output() reads state["formatted_output"] to build
    # the invoke() result.  A non-declared key is silently dropped by LangGraph.
    formatted_output: Optional[str]

    # Error details (non-fatal; complements error_log inherited from AgentState)
    error_message: Optional[str]
    error_code: Optional[str]
