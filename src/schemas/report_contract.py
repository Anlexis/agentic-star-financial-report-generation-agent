"""AgentCore Platform v1.0"""

# FIN-C2-007 — the caller-data contract for financial report generation.
#
# Single source of truth for:
#   * which report types exist and which fields each one requires;
#   * how every caller-supplied value is bounded before it is trusted;
#   * which fields are monetary, and the precision grid the external report
#     is published on.
#
# Every value that reaches this module arrives from an untrusted caller, so
# each field is validated against an explicit, closed contract:
#
#   numbers      finite (never NaN or +/-Infinity) and inside a declared range;
#                booleans are rejected even though Python treats them as ints.
#   identifiers  an inert character set with a length cap, so caller text can
#                never carry document structure (Markdown, HTML, table pipes,
#                newlines) into the rendered report.
#   dates        strict ISO calendar dates.
#   enums        a closed set of accepted values.
#
# Validation failures name the FIELD and never repeat the rejected value:
# echoing it back would put caller-controlled text into logs and error output.

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

# ── External precision grid ───────────────────────────────────────────────
# Monetary figures are published in units of 1,000: the report RENDERS on
# this grid and the output boundary independently ENFORCES it.
EXTERNAL_ROUND_UNIT = 1000

# ── Caller-string contract ────────────────────────────────────────────────
# Identifiers render verbatim into the report, so the accepted alphabet is
# inert: letters, digits, underscore and hyphen only.  At least one letter is
# required as well — a purely numeric identifier is indistinguishable from a
# monetary figure at the output boundary and would be snapped onto the
# precision grid, silently rewriting the caller's own reference.
IDENT_MAX_LEN = 32
_IDENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,%d}$" % IDENT_MAX_LEN)
_HAS_LETTER_RE = re.compile(r"[A-Za-z]")
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

# Largest number of entries accepted in a caller data payload.
MAX_PAYLOAD_ENTRIES = 32

# Field kinds
KIND_IDENT = "identifier"
KIND_NUMBER = "number"
KIND_DATE = "date"
KIND_ENUM = "enum"

# Report types and their field contracts.  Ranges are inclusive.
FIELD_SPECS: Dict[str, List[Dict[str, Any]]] = {
    "risk_assessment": [
        {"name": "portfolio_id", "kind": KIND_IDENT},
        {"name": "risk_score", "kind": KIND_NUMBER, "min": 0.0, "max": 10.0},
        {
            "name": "total_exposure",
            "kind": KIND_NUMBER,
            "min": 0.0,
            "max": 1e15,
            "monetary": True,
        },
        {"name": "currency", "kind": KIND_ENUM, "values": ("JPY", "USD", "EUR", "GBP")},
    ],
    "credit_summary": [
        {"name": "client_id", "kind": KIND_IDENT},
        {"name": "credit_score", "kind": KIND_NUMBER, "min": 300.0, "max": 850.0},
        {"name": "dti_ratio", "kind": KIND_NUMBER, "min": 0.0, "max": 100.0},
        {"name": "credit_utilization", "kind": KIND_NUMBER, "min": 0.0, "max": 100.0},
    ],
    "portfolio_performance": [
        {"name": "portfolio_id", "kind": KIND_IDENT},
        {"name": "total_return", "kind": KIND_NUMBER, "min": -1000.0, "max": 10000.0},
        {"name": "period_start", "kind": KIND_DATE},
        {"name": "period_end", "kind": KIND_DATE},
    ],
    "compliance_status": [
        {"name": "entity_id", "kind": KIND_IDENT},
        {"name": "reporting_period", "kind": KIND_IDENT},
        {"name": "regulation_name", "kind": KIND_IDENT},
        {
            "name": "compliance_status",
            "kind": KIND_ENUM,
            "values": ("COMPLIANT", "NON_COMPLIANT", "UNDER_REVIEW"),
        },
    ],
}

SUPPORTED_REPORT_TYPES = frozenset(FIELD_SPECS)

# Fields whose value is a monetary amount — rendered on the precision grid.
MONETARY_FIELDS = frozenset(spec["name"] for specs in FIELD_SPECS.values() for spec in specs if spec.get("monetary"))


def snap_to_grid(value: float) -> int:
    """Round a monetary amount onto the published precision grid."""
    return int(round(value / EXTERNAL_ROUND_UNIT) * EXTERNAL_ROUND_UNIT)


def finite_in_range(value: Any, *, field: str, minimum: float, maximum: float) -> Tuple[Optional[float], Optional[str]]:
    """Accept a caller number only when it is finite and inside [minimum, maximum].

    Fails CLOSED: anything that is not a real, finite, in-range number is
    rejected with a field-naming error and no value echo.

    Booleans are rejected explicitly (``isinstance(True, int)`` is True in
    Python).  NaN and +/-Infinity are rejected explicitly as well: they survive
    ``float()`` and every ordering comparison against them evaluates False, so
    a plain ``value < minimum`` range check would let them through and they
    would then suppress whichever threshold decision they take part in.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, f"field '{field}' must be a number"
    try:
        numeric = float(value)
    except (OverflowError, ValueError):
        # A JSON integer too large to convert is out of range by definition.
        return None, f"field '{field}' is outside the permitted range"
    if not math.isfinite(numeric):
        return None, f"field '{field}' must be a finite number"
    if numeric < minimum or numeric > maximum:
        return None, f"field '{field}' is outside the permitted range"
    # The original value is returned, not the float() probe, so an integer
    # amount keeps rendering as an integer.
    return value, None


def valid_identifier(value: Any, *, field: str) -> Tuple[Optional[str], Optional[str]]:
    """Accept a caller identifier only in the inert alphabet, with a letter in it."""
    if not isinstance(value, str):
        return None, f"field '{field}' must be a string"
    if not _IDENT_RE.match(value):
        return None, (
            f"field '{field}' must be 1-{IDENT_MAX_LEN} characters " f"from letters, digits, underscore and hyphen"
        )
    if not _HAS_LETTER_RE.search(value):
        return None, f"field '{field}' must contain at least one letter"
    return value, None


def valid_iso_date(value: Any, *, field: str) -> Tuple[Optional[str], Optional[str]]:
    """Accept a caller date only as a real ISO calendar date (YYYY-MM-DD)."""
    if not isinstance(value, str):
        return None, f"field '{field}' must be a string"
    match = _ISO_DATE_RE.match(value)
    if not match:
        return None, f"field '{field}' must be an ISO date (YYYY-MM-DD)"
    try:
        date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None, f"field '{field}' is not a real calendar date"
    return value, None


def valid_enum(value: Any, *, field: str, allowed: Tuple[str, ...]) -> Tuple[Optional[str], Optional[str]]:
    """Accept a caller value only when it is one of the declared options."""
    if not isinstance(value, str) or value not in allowed:
        return None, f"field '{field}' must be one of: {', '.join(allowed)}"
    return value, None


def validate_payload(report_type: str, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Validate a caller payload against the contract for *report_type*.

    Returns ``(normalised, errors)``.  ``normalised`` holds only the fields the
    contract declares, so unknown caller keys never reach the report.  Error
    strings name the offending field and never repeat its value.
    """
    specs = FIELD_SPECS.get(report_type)
    if specs is None:
        return {}, ["report_type is not a supported report type"]

    if len(payload) > MAX_PAYLOAD_ENTRIES:
        return {}, [f"input_data carries more than {MAX_PAYLOAD_ENTRIES} entries"]

    normalised: Dict[str, Any] = {}
    errors: List[str] = []
    for spec in specs:
        name = spec["name"]
        if name not in payload:
            errors.append(f"field '{name}' is required for this report type")
            continue
        raw = payload[name]
        kind = spec["kind"]
        value: Any
        error: Optional[str]
        if kind == KIND_NUMBER:
            value, error = finite_in_range(raw, field=name, minimum=spec["min"], maximum=spec["max"])
        elif kind == KIND_IDENT:
            value, error = valid_identifier(raw, field=name)
        elif kind == KIND_DATE:
            value, error = valid_iso_date(raw, field=name)
        else:
            value, error = valid_enum(raw, field=name, allowed=spec["values"])
        if error:
            errors.append(error)
        else:
            normalised[name] = value

    return ({}, errors) if errors else (normalised, [])
