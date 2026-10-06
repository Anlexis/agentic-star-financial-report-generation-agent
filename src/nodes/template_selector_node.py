"""AgentCore Platform v1.0"""

# FIN-C2-007 — TemplateSelectorNode
#
# Maps the validated report_type to its document template, loaded from
# config/templates/{report_type}.md.  Template files carry ${FIELD_NAME}
# markers that ReportDraftNode fills in with validated financial data.
#
# Falls back to a minimal inline template when the file is unavailable, so the
# pipeline still produces a document in an environment without the config
# directory.
#
# Audit: emit_trace_event() on template selection.

import os
from typing import Any, ClassVar, Dict, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.report_contract import SUPPORTED_REPORT_TYPES

# config/templates/ relative to this file (src/nodes/ -> ../../config/templates/)
_TEMPLATES_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", "templates")
)

# Minimal inline fallbacks used when the template file is unavailable.
_FALLBACK_TEMPLATES: Dict[str, str] = {
    "risk_assessment": (
        "# Risk Assessment Report\n\n"
        "Portfolio: ${PORTFOLIO_ID}\n"
        "Risk Score: ${RISK_SCORE}\n"
        "Total Exposure: ${CURRENCY} ${TOTAL_EXPOSURE}\n\n"
        "## Analysis\n${RISK_ANALYSIS_NARRATIVE}\n"
    ),
    "credit_summary": (
        "# Credit Summary Report\n\n"
        "Client: ${CLIENT_ID}\n"
        "Credit Score: ${CREDIT_SCORE}\n"
        "DTI Ratio: ${DTI_RATIO}%\n"
        "Credit Utilization: ${CREDIT_UTILIZATION}%\n\n"
        "## Summary\n${CREDIT_NARRATIVE}\n"
    ),
    "portfolio_performance": (
        "# Portfolio Performance Report\n\n"
        "Portfolio: ${PORTFOLIO_ID}\n"
        "Period: ${PERIOD_START} to ${PERIOD_END}\n"
        "Total Return: ${TOTAL_RETURN}%\n\n"
        "## Commentary\n${PERFORMANCE_COMMENTARY}\n"
    ),
    "compliance_status": (
        "# Compliance Status Report\n\n"
        "Entity: ${ENTITY_ID}\n"
        "Period: ${REPORTING_PERIOD}\n"
        "Regulation: ${REGULATION_NAME}\n"
        "Status: ${COMPLIANCE_STATUS}\n\n"
        "## Findings\n${COMPLIANCE_FINDINGS}\n"
        "## Action Items\n${ACTION_ITEMS}\n"
    ),
}


def _load_template(report_type: str) -> str:
    """Load the template file for *report_type*; fall back to the inline default."""
    template_path = os.path.join(_TEMPLATES_DIR, f"{report_type}.md")
    if os.path.isfile(template_path):
        with open(template_path, encoding="utf-8") as fh:
            return fh.read()
    return _FALLBACK_TEMPLATES[report_type]


class TemplateSelectorNode(FunctionNode):
    """Select the document template for the requested report_type.

    Reads report_type from state, loads the matching template from
    config/templates/, and writes its content as selected_template for
    ReportDraftNode to consume.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # A reason settled earlier in the run is the real one: pass it through
        # untouched instead of doing work on input that was already declined.
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        report_type = state.get("report_type", "")

        if not report_type:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["TemplateSelectorNode: report_type missing from state"],
            }

        # report_type reaches a filesystem path below, so it is re-checked
        # against the closed set here as well as at the input gate: this node
        # holds the guarantee whether or not an earlier node ran.
        if report_type not in SUPPORTED_REPORT_TYPES:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["TemplateSelectorNode: report_type is not a supported report type"],
            }

        template_content = _load_template(report_type)

        emit_trace_event(
            "financial_report_template_selected",
            {"report_type": report_type, "template_chars": len(template_content)},
            state,
        )

        return {
            "selected_template": template_content,
            "status": AgentStatus.SUCCESS.value,
        }
