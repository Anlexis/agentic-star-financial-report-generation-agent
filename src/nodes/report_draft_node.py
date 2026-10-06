"""AgentCore Platform v1.0"""

# FIN-C2-007 — ReportDraftNode
#
# Merges validated financial data into the selected document template.
#
# Templates carry ${FIELD_NAME} substitution markers.  Each marker is replaced
# exactly once, from a map built out of validated_data; markers with no
# matching field render as "[TBD]" so an incomplete report is visibly
# incomplete rather than silently wrong.
#
# Published precision: monetary amounts are rendered on the external grid
# (units of 1,000) and the report states that it is on that grid.  Line-item
# precision is a caller-internal detail and never reaches the document — the
# output boundary independently enforces the same grid.
#
# Narrative sections receive deterministic text: this template ships without a
# model backend, and the narrative map is the seam where one would be attached.
#
# Audit: emit_trace_event() on draft completion.

import json
import re
from typing import Any, ClassVar, Dict, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.report_contract import (
    EXTERNAL_ROUND_UNIT,
    MONETARY_FIELDS,
    snap_to_grid,
)

# Substitution markers in template files: ${UPPER_SNAKE}
_PLACEHOLDER_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")

# Sentence appended to any report that renders a monetary amount, so a reader
# knows the figures are grid values and not full-precision balances.
PRECISION_NOTE = (
    f"Monetary figures in this report are expressed on a grid of " f"{EXTERNAL_ROUND_UNIT:,d} currency units."
)

# Deterministic narrative text for the report's prose sections.
_NARRATIVE_SECTIONS: Dict[str, str] = {
    "RISK_ANALYSIS_NARRATIVE": (
        "Based on the submitted portfolio data, the risk profile has been assessed. "
        "Key risk drivers and mitigation strategies are documented in the sections below."
    ),
    "CREDIT_NARRATIVE": (
        "The credit assessment reflects current financial obligations and repayment history. "
        "Recommended credit limits and conditions are provided based on the scoring model."
    ),
    "PERFORMANCE_COMMENTARY": (
        "Portfolio performance over the reporting period reflects market conditions and "
        "asset allocation decisions. Comparative analysis against benchmark is included."
    ),
    "COMPLIANCE_FINDINGS": (
        "The compliance review identified the following items requiring attention. "
        "Each finding is categorized by severity and associated regulatory requirement."
    ),
    "RECOMMENDATIONS": (
        "Recommended actions follow from the indicators above. Each recommendation "
        "is stated with the exposure or score that motivates it."
    ),
    "ASSET_ALLOCATION": (
        "Allocation across asset classes for the reporting period is summarised here, "
        "together with the drift from the portfolio's target weights."
    ),
    "ACTION_ITEMS": (
        "The following corrective actions are required to achieve full compliance status. "
        "Completion deadlines and responsible parties are noted for each item."
    ),
}


def _render_value(field: str, value: Any) -> str:
    """Render one validated field for the document.

    Monetary amounts are snapped onto the published grid and grouped; every
    other field renders as its plain string form.
    """
    if field in MONETARY_FIELDS and isinstance(value, (int, float)):
        return f"{snap_to_grid(float(value)):,d}"
    return str(value)


def _build_substitution_map(data: Dict[str, Any]) -> Dict[str, str]:
    """Map ${FIELD} markers to rendered values, then to narrative text."""
    mapping: Dict[str, str] = {field.upper(): _render_value(field, value) for field, value in data.items()}
    for section, text in _NARRATIVE_SECTIONS.items():
        mapping.setdefault(section, text)
    return mapping


def _fill_template(template: str, substitutions: Dict[str, str]) -> str:
    """Replace every ${MARKER}; markers with no value render as '[TBD]'."""

    def replace(match: "re.Match[str]") -> str:
        return substitutions.get(match.group(1), "[TBD]")

    return _PLACEHOLDER_RE.sub(replace, template)


class ReportDraftNode(FunctionNode):
    """Generate the report draft from validated data and the selected template.

    Reads validated_data (JSON string) and selected_template from state and
    writes the rendered document as report_draft.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # A reason settled earlier in the run is the real one: pass it through
        # untouched instead of doing work on input that was already declined.
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        validated_data_str = state.get("validated_data", "")
        # state.get() without a default so None means "never set"; an empty
        # string is a valid (trivially empty) template and must not be treated
        # as missing.
        selected_template = state.get("selected_template")
        report_type = state.get("report_type", "unknown")

        if not validated_data_str:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ReportDraftNode: validated_data missing from state"],
            }

        if selected_template is None:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ReportDraftNode: selected_template missing from state"],
            }

        try:
            validated_data = json.loads(validated_data_str)
        except json.JSONDecodeError:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ReportDraftNode: validated_data is not valid JSON"],
            }

        substitutions = _build_substitution_map(validated_data)
        report_draft = _fill_template(selected_template, substitutions)

        if any(field in MONETARY_FIELDS for field in validated_data):
            report_draft = f"{report_draft.rstrip()}\n\n{PRECISION_NOTE}\n"

        emit_trace_event(
            "financial_report_draft_generated",
            {
                "report_type": report_type,
                "draft_chars": len(report_draft),
                "fields_rendered": len(substitutions),
            },
            state,
        )

        return {
            "report_draft": report_draft,
            "status": AgentStatus.SUCCESS.value,
        }
