"""AgentCore Platform v1.0"""

# FIN-C2-007 — PostProcessNode
#
# The external-output boundary for the generated financial report.  Three
# independent layers run in a fixed order, and the order is load-bearing:
#
#   (1) disallowed-content scan — API keys, JWTs, Bearer tokens, credential
#       assignments and government identifier numbers anywhere in the document
#       withhold the report entirely (sanitised stub, status ERROR);
#   (2) verbatim caller-text redaction — the document is assembled from
#       validated fields and template prose, so a verbatim embedding of the
#       caller's raw request text is a leak, not a feature: it is replaced
#       with [REDACTED];
#   (3) monetary precision grid — the published schema expresses monetary
#       figures in units of 1,000; every monetary-form token is snapped onto
#       that grid, with an audit event per snap.
#
# Layer (1) runs BEFORE layer (3) and is then re-run afterwards.  The snap
# rewrites digits, so a scan placed only after it can be blind to a pattern
# the snap has already mangled; running it first catches the pattern intact,
# and running it again afterwards proves the snap introduced nothing new.
# The identifier guards in the grammar below close the same gap at source —
# a hyphenated identifier such as "123-45-6789" is not a monetary token, so
# the snap leaves it byte-identical and both scans see the same text.
#
# The domain output gate is the module-level function _security_gate_output()
# called from inside execute() — NOT an instance method on the node class:
# the framework auto-wraps node instance methods on the real invoke path.
#
# Returns ONLY the state keys this node writes (partial-dict contract).

import logging
import re
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.failure_message import EMPTY_INPUT, INPUT_REJECTED, INVALID_VALUE, TOO_LONG
from src.schemas.report_contract import EXTERNAL_ROUND_UNIT

logger = logging.getLogger(__name__)

# Content that must never appear in a published report.  The government
# identifier pattern is deliberately shaped (three-two-four with hyphens):
# ISO dates (four-two-two) and telephone groupings do not match it.
_DISALLOWED_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("api_key", re.compile(r"\b(?:sk|pk|ak)-[A-Za-z0-9]{16,}", re.IGNORECASE)),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9._~+/]{20,}", re.IGNORECASE)),
    (
        "credential_assignment",
        re.compile(
            r"\b(?:password|passwd|secret|api_key|token|access_key|private_key)\s*[:=]\s*\S{8,}",
            re.IGNORECASE,
        ),
    ),
    ("government_identifier", re.compile(r"(?<![\w-])\d{3}-\d{2}-\d{4}(?![\w-])")),
]

_BLOCKED_STUB = (
    "[REPORT WITHHELD — disallowed content was detected in the generated financial "
    "report. Review the submitted data and retry without credential-like strings.]"
)

# State fields that must never be embedded verbatim in the published report.
# The document is assembled from validated fields and template prose; the
# caller's raw request text re-appearing verbatim means caller-controlled
# content reached the external surface.
_BLOCKED_FIELDS = frozenset({"user_input", "validated_input"})

# ── Published monetary precision ──────────────────────────────────────────
# Monetary figures are published in units of 1,000 (the report RENDERS on this
# grid — see src/nodes/report_draft_node.py — and this gate ENFORCES it).
#
# A monetary value is identified by FORM and by CURRENCY CONTEXT, never by
# magnitude:
#   form:    comma-grouped numbers (9,999 / 1,234,567) and unformatted runs of
#            5 or more digits (a rendering-regression leak);
#   context: any bare 1-4 digit number next to a currency marker is monetary
#            even though it is short — SYMMETRICALLY: a standalone three-letter
#            uppercase code or a currency symbol (including fullwidth
#            characters), before or after the value, attached or separated,
#            signed or unsigned.
#
# IDENTIFIER GUARDS.  Reports render caller identifiers (portfolio, client and
# entity references such as PF-2024-001 or PF_48210) over the alphabet
# [A-Za-z0-9_-].  A monetary token never begins or ends INSIDE such an
# identifier, so the whole grammar is wrapped in single-character guards over
# exactly that alphabet — the underscore included, because this template's
# identifiers use it and a bare digit run after one would otherwise snap.
# Identifiers are additionally required to carry at least one letter (see
# src/schemas/report_contract.py), which is what makes the guards sufficient:
# a purely numeric reference has no neighbouring identifier character to
# protect it and is rejected at input instead.
#
# Structural tokens stay untouched: percentages, scores, counts, years, and
# acronyms embedded in words.
#
# Grammar notes: group-based, with no variable-width lookbehinds, so the
# marker/value delimiter can be an arbitrary run of horizontal whitespace.
# Branch order matters — currency-context branches first, then form-based.
_CURRENCY_MARKER = r"(?:\b[A-Z]{3}|[¥￥$€£円₩])"
# Delimiter between a currency marker and its value: horizontal whitespace and
# at most ONE newline — never a paragraph break.  A plain \s* spans blank
# lines, so a three-letter uppercase word ending a line would bind to the
# number that opens the next block and rewrite it: "Currency: JPY\n\n3. Cash
# Position" would become "0. Cash Position", i.e. the gate would rewrite the
# document's own section numbering.
_GATE_DELIM = r"[ \t]*(?:\n[ \t]*)?"

# A monetary amount may carry a decimal part, and the value alternatives absorb
# it as part of the same token. Without that, the fraction of "9999.99999" is a
# standalone five-digit run: the guards would admit it (a decimal point is not
# an identifier character) and the snap would rewrite a percentage into
# "9999.100,000". The decimal point therefore also joins the LEADING guard, so
# no match can begin inside a fraction — but not the trailing one, or an amount
# ending a sentence would escape.
# Absorption must be all-or-nothing: with a plain `(?:\.\d+)?` the engine can
# BACKTRACK out of the fraction and re-match the integer part alone whenever
# the character after the fraction fails the trailing guard, so a suffixed
# "JPY 1234.56m" snapped to "JPY 1,000.56m" all over again. The second arm
# asserts there is no fraction to leave behind.
_VAL_FRACTION = r"(?:\.\d+|(?!\.\d))"

_NUM_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    # marker THEN value: "JPY 9999", "JPY  -9999", "JPY\t9999", "¥9999".
    # The value alternatives accept the comma-grouped form FIRST: the regex is
    # leftmost-first, so without it "JPY 1,234" would match as marker + "1"
    # and the snap would rewrite the number to "JPY 0,234" — an on-grid
    # "JPY 1,000" must stay byte-identical and an off-grid "JPY 1,234" must
    # snap as 1234, not as 1.
    rf"(?:(?P<pre>{_CURRENCY_MARKER}{_GATE_DELIM})"
    rf"(?P<val_after>[+-]?\d{{1,3}}(?:,\d{{3}})+{_VAL_FRACTION}|[+-]?\d{{1,4}}{_VAL_FRACTION})"
    # value THEN marker: "9999 JPY", "-9999\tJPY", "9999円", "+9999  $"
    rf"|(?P<val_before>[+-]?\d{{1,4}}{_VAL_FRACTION})"
    rf"(?P<post>{_GATE_DELIM}(?:[A-Z]{{3}}\b|[¥￥$€£円₩]))"
    # form-based, standalone at any magnitude: comma-grouped or 5+-digit runs
    rf"|(?P<val_form>[+-]?\d{{1,3}}(?:,\d{{3}})+{_VAL_FRACTION}|[+-]?\d{{5,}}{_VAL_FRACTION}))"
    r"(?![A-Za-z0-9_-])"
)


def _enforce_precision(result: str) -> Tuple[str, int]:
    """Snap every monetary-form token onto the published external grid.

    Returns (sanitised_result, snap_count).  A snap means a full-precision
    monetary figure reached the external surface and was rounded onto the
    published grid.  The currency marker, the original delimiter whitespace
    and the explicit sign of the original token are all preserved.
    """
    snaps = 0

    def _snap(match: "re.Match[str]") -> str:
        nonlocal snaps
        pre = match.group("pre") or ""
        post = match.group("post") or ""
        token = match.group("val_after") or match.group("val_before") or match.group("val_form")
        value = float(token.replace(",", ""))  # float() understands a leading +/-
        if value % EXTERNAL_ROUND_UNIT == 0:
            return match.group(0)
        snaps += 1
        snapped = round(value / EXTERNAL_ROUND_UNIT) * EXTERNAL_ROUND_UNIT
        plus = "+" if token.startswith("+") and snapped >= 0 else ""
        return f"{pre}{plus}{snapped:,d}{post}"

    return _NUM_TOKEN_RE.sub(_snap, result), snaps


def _security_gate_output(content: str) -> Optional[str]:
    """Scan a report for disallowed content.

    Returns the name of the first violation, or None when the report is clean.
    Module-level function (not a node instance method) — the framework
    auto-wraps node instance methods on the real invoke path.
    """
    for name, pattern in _DISALLOWED_PATTERNS:
        if pattern.search(content):
            return name
    return None


def _redact_blocked_fields(result: str, state: AgentState) -> Tuple[str, List[str]]:
    """Replace verbatim embeddings of caller request text with [REDACTED].

    Returns (sanitised_result, redacted_field_names).  Only substantial values
    (longer than 10 characters) are matched, so short incidental overlaps with
    ordinary report prose are left alone.
    """
    redacted: List[str] = []
    sanitised = result
    for field in sorted(_BLOCKED_FIELDS):
        value = state.get(field)
        if isinstance(value, str) and len(value) > 10 and value in sanitised:
            sanitised = sanitised.replace(value, "[REDACTED]")
            redacted.append(field)
    return sanitised, redacted


# Reason code -> the sentence the caller reads. A code with no entry falls
# back to the generic one rather than leaking the code itself.
_DEGRADED_MESSAGES = {
    "EMPTY_INPUT": EMPTY_INPUT,
    "QUESTION_TOO_LONG": TOO_LONG,
    "INVALID_REQUEST": INVALID_VALUE,
}


class PostProcessNode(FunctionNode):
    """Apply the output boundary and publish the final financial report.

    Declared ANONYMOUS — caller trust was already enforced at PreProcessNode.

    Input state keys:
        report_draft: str — the rendered document from ReportDraftNode

    Output state keys (partial dict):
        formatted_output: str
        status:           str
        error_log:        list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # A run declined upstream has nothing to format. Render the reason as
        # the caller-facing body and carry the marker onward.
        marker = state.get("error_code")
        if marker:
            message = _DEGRADED_MESSAGES.get(marker, INPUT_REJECTED)
            emit_trace_event("post_process_degraded", {"reason": marker}, state)
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": marker,
                "output": message,
                "formatted_output": message,
            }
        content = state.get("report_draft") or ""

        if not content.strip():
            # Nothing was drafted — publish nothing rather than improvising a
            # document out of internal state.
            return {
                "formatted_output": "",
                "status": AgentStatus.SUCCESS.value,
            }

        # ── Layer 1: disallowed-content scan (withhold entirely) ───────────
        violation = _security_gate_output(content)
        if violation:
            logger.error("PostProcessNode: disallowed content in report — %s", violation)
            emit_trace_event(
                "financial_report_content_violation",
                {"violation": violation},
                state,
            )
            return {
                "formatted_output": _BLOCKED_STUB,
                "report_draft": _BLOCKED_STUB,
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PostProcessNode: report withheld — disallowed content ({violation})"],
            }

        # ── Layer 2: verbatim caller-text redaction ────────────────────────
        sanitised, redacted_fields = _redact_blocked_fields(content, state)
        if redacted_fields:
            logger.error(
                "PostProcessNode: caller request text embedded verbatim in report — %s",
                ", ".join(redacted_fields),
            )
            emit_trace_event(
                "financial_report_verbatim_redaction",
                {"fields": redacted_fields},
                state,
            )

        # ── Layer 3: monetary precision grid ───────────────────────────────
        sanitised, snaps = _enforce_precision(sanitised)
        if snaps:
            logger.warning(
                "PostProcessNode: %d off-grid monetary token(s) snapped onto the published grid",
                snaps,
            )
            emit_trace_event(
                "financial_report_precision_snap",
                {"snap_count": snaps},
                state,
            )

        # ── Re-scan: the snap rewrote digits, so layer 1 runs again ────────
        violation = _security_gate_output(sanitised)
        if violation:
            logger.error("PostProcessNode: disallowed content after grid enforcement — %s", violation)
            emit_trace_event(
                "financial_report_content_violation",
                {"violation": violation},
                state,
            )
            return {
                "formatted_output": _BLOCKED_STUB,
                "report_draft": _BLOCKED_STUB,
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PostProcessNode: report withheld — disallowed content ({violation})"],
            }

        emit_trace_event(
            "financial_report_emitted",
            {
                "report_type": state.get("report_type", "unknown"),
                "report_chars": len(sanitised),
            },
            state,
        )

        return {
            "formatted_output": sanitised,
            "status": AgentStatus.SUCCESS.value,
        }
