"""AgentCore Platform v1.0"""

# FIN-C2-007 — PreProcessNode: the external input boundary.
#
# Node contract:
#  - Extend FunctionNode; implement execute(state, config=None) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum string values (AgentStatus.SUCCESS.value)
#
# Caller-data channels.  A request may supply its report request either as a
# JSON object in user_input or through the invocation's structured context
# channel (input_context), or both — context values win, because they are the
# explicit structured channel.  At least one channel must supply a supported
# report_type and an input_data object; a request that supplies neither is
# rejected rather than answered from nothing.
#
# The framework input gate masks PII in user_input before execute() runs and
# rejects high-confidence injection there.  It does NOT look at the context
# channel, so this node screens context text itself — a template owns its own
# guarantees rather than assuming a gate in front of it.  For the same reason
# the injection and PII screens below run unconditionally, so a direct
# execute() call with no framework wrapper is screened identically.
#
# Rejections name the offending field and never repeat the rejected value.
#
# required_trust_level = VERIFIED_EXTERNAL — this is the external boundary;
# only verified external callers may initiate report generation.

import json
import re
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.progress import emit_progress
from src.services.failure_message import EMPTY_INPUT, INPUT_REJECTED, INVALID_VALUE, TOO_LONG
from src.schemas.report_contract import MAX_PAYLOAD_ENTRIES, SUPPORTED_REPORT_TYPES

# Structural limits on the request itself, ahead of any parsing.
MAX_INPUT_CHARS = 64 * 1024
MAX_CONTEXT_CHARS = 64 * 1024

# Prompt-injection forms rejected at the input boundary.  Each pattern
# describes an instruction aimed at the agent, not ordinary financial prose:
# the screens must not fire on legitimate report requests.
_INJECTION_PATTERNS: List["re.Pattern[str]"] = [
    re.compile(r"ignore\s+(?:all\s+)?(?:the\s+)?previous\s+instructions?", re.IGNORECASE),
    re.compile(r"ignore\s+all\s+previous", re.IGNORECASE),
    re.compile(r"\bsystem\s*prompt\b", re.IGNORECASE),
    re.compile(r"<\s*/?(?:script|iframe|img|svg)\b", re.IGNORECASE),
    re.compile(r"\bexec\s*\(", re.IGNORECASE),
]

# Personal data that must not enter a financial report request.
#
# The telephone forms are bounded on both sides so that a legitimate reference
# such as PF-0120-3456 or IDX-0999-1234 — a hyphenated identifier that merely
# contains a digit group starting with zero — is not read as a phone number.
# The separator-less alternative carries its own length anchor for the same
# reason: without it, an ordinary six-digit amount would match.
_PII_PATTERNS: List["re.Pattern[str]"] = [
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),  # email address
    re.compile(r"(?<![\w-])0\d{1,3}-\d{3,4}-\d{3,4}(?![\w-])"),  # telephone, grouped
    re.compile(r"(?<![\w-])0\d{9,10}(?![\w-])"),  # telephone, ungrouped
    re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?)\b"),  # payment card
    re.compile(r"\b5[1-5][0-9]{14}\b"),  # payment card
]

# Context keys this node accepts from the structured channel.
_CONTEXT_REPORT_TYPE = "report_type"
_CONTEXT_INPUT_DATA = "input_data"


def _screen(text: str) -> Optional[str]:
    """Return a rejection reason for *text*, or None when it is acceptable."""
    if "[MASKED]" in text:
        return "personal data was detected and masked"
    for pattern in _PII_PATTERNS:
        if pattern.search(text):
            return "personal data was detected"
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            return "a prompt-injection pattern was detected"
    return None


class PreProcessNode(FunctionNode):
    """Validate and screen an incoming financial report request.

    Accepts the report request from user_input and/or the structured context
    channel, screens both for personal data and injection attempts, checks the
    report type against the supported set, and writes the extracted request to
    state for the report pipeline.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        user_input = state.get("user_input", "")
        if not isinstance(user_input, str):
            user_input = ""

        raw_context = state.get("input_context") or {}
        if not isinstance(raw_context, dict):
            emit_progress(INPUT_REJECTED)
            # A value the caller can correct: nothing downstream runs, and the
            # run COMPLETES carrying the reason so the request can be sent again.
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": "INVALID_REQUEST",
                "error_log": ["PreProcessNode: input_context must be an object"],
            }

        if len(user_input) > MAX_INPUT_CHARS:
            emit_progress(TOO_LONG)
            # A value the caller can correct: nothing downstream runs, and the
            # run COMPLETES carrying the reason so the request can be sent again.
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": "QUESTION_TOO_LONG",
                "error_log": [f"PreProcessNode: user_input exceeds {MAX_INPUT_CHARS} characters"],
            }

        try:
            context_text = json.dumps(raw_context, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            emit_progress(INPUT_REJECTED)
            # A value the caller can correct: nothing downstream runs, and the
            # run COMPLETES carrying the reason so the request can be sent again.
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": "INVALID_REQUEST",
                "error_log": ["PreProcessNode: input_context is not serialisable"],
            }
        if len(context_text) > MAX_CONTEXT_CHARS:
            emit_progress(TOO_LONG)
            # A value the caller can correct: nothing downstream runs, and the
            # run COMPLETES carrying the reason so the request can be sent again.
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": "QUESTION_TOO_LONG",
                "error_log": [f"PreProcessNode: input_context exceeds {MAX_CONTEXT_CHARS} characters"],
            }

        if not user_input.strip() and not raw_context:
            emit_progress(EMPTY_INPUT)
            # A value the caller can correct: nothing downstream runs, and the
            # run COMPLETES carrying the reason so the request can be sent again.
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": "EMPTY_INPUT",
                "error_log": ["PreProcessNode: no report request was supplied"],
            }

        # Screen both caller channels.  The framework gate covers user_input
        # only, so the context channel is screened here as well.
        for channel, text in (("user_input", user_input), ("input_context", context_text)):
            reason = _screen(text)
            if reason:
                return {
                    "status": AgentStatus.ERROR.value,
                    "error_log": [f"PreProcessNode: request rejected — {reason} in {channel}"],
                }

        # Assemble the request: the JSON body first, then the structured
        # channel on top of it.
        request: Dict[str, Any] = {}
        if user_input.strip():
            try:
                parsed = json.loads(user_input.strip())
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                request.update(parsed)
            elif not raw_context:
                emit_progress(INVALID_VALUE)
                # A value the caller can correct: nothing downstream runs, and the
                # run COMPLETES carrying the reason so the request can be sent again.
                return {
                    "status": AgentStatus.SUCCESS.value,
                    "error_code": "INVALID_REQUEST",
                    "error_log": ["PreProcessNode: user_input is not a JSON object"],
                }

        for key in (_CONTEXT_REPORT_TYPE, _CONTEXT_INPUT_DATA):
            if key in raw_context:
                request[key] = raw_context[key]

        report_type = request.get("report_type", "")
        if not report_type or not isinstance(report_type, str):
            emit_progress(INVALID_VALUE)
            # A value the caller can correct: nothing downstream runs, and the
            # run COMPLETES carrying the reason so the request can be sent again.
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": "INVALID_REQUEST",
                "error_log": ["PreProcessNode: field 'report_type' is missing or not a string"],
            }
        report_type = report_type.strip().lower()
        if report_type not in SUPPORTED_REPORT_TYPES:
            emit_progress(INVALID_VALUE)
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": "INVALID_REQUEST",
                "error_log": [
                    "PreProcessNode: field 'report_type' must be one of: " + ", ".join(sorted(SUPPORTED_REPORT_TYPES))
                ],
            }

        input_data = request.get(_CONTEXT_INPUT_DATA)
        if not isinstance(input_data, dict) or not input_data:
            emit_progress(INVALID_VALUE)
            # A value the caller can correct: nothing downstream runs, and the
            # run COMPLETES carrying the reason so the request can be sent again.
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": "INVALID_REQUEST",
                "error_log": ["PreProcessNode: field 'input_data' is missing or not an object"],
            }
        if len(input_data) > MAX_PAYLOAD_ENTRIES:
            emit_progress(INVALID_VALUE)
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": "INVALID_REQUEST",
                "error_log": [
                    f"PreProcessNode: field 'input_data' carries more than " f"{MAX_PAYLOAD_ENTRIES} entries"
                ],
            }

        # Structured fields travel as JSON strings for checkpoint safety.
        input_data_str = json.dumps(input_data, ensure_ascii=False)

        emit_trace_event(
            "financial_report_request_accepted",
            {
                "report_type": report_type,
                # Key NAMES only, each truncated: the audit trail records the
                # shape of a request, never its contents.
                "input_data_keys": sorted(str(key)[:64] for key in input_data),
            },
            state,
        )

        return {
            "validated_input": user_input.strip(),
            "report_type": report_type,
            "input_data": input_data_str,
            "status": AgentStatus.SUCCESS.value,
        }
