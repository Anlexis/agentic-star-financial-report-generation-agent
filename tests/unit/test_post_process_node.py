"""FIN-C2-007 — Unit tests: PostProcessNode, the external output boundary.

Nodes are invoked through BaseNode.__call__ (`node(state)`) rather than
execute() directly, so the framework trust gate runs on every unit invocation.
PostProcessNode is a backbone inner node, so it admits ANONYMOUS callers.
The disallowed-content payloads below are not input-scan fields, so they reach
execute() intact and the document gate is what blocks them.

The precision-grid cases are probed in BOTH directions: every monetary form
must snap, and every structural token must survive byte-identical.
"""

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.post_process_node import (
    _enforce_precision,
    _security_gate_output,
    PostProcessNode,
)

# Inner-node caller trust (ANONYMOUS) so BaseNode.__call__'s trust gate admits it.
_AN = TrustLevel.ANONYMOUS.value

_CLEAN_DRAFT = (
    "# Risk Assessment Report\n\n"
    "**Portfolio ID**: PF-001\n"
    "**Currency**: JPY\n\n"
    "Risk Score: **7.5 / 10**\n\n"
    "Total Portfolio Exposure: JPY 1,500,000\n"
)


@pytest.fixture()
def node():
    return PostProcessNode()


def _state(draft: str, **extra) -> dict:
    state = {"report_draft": draft, "caller_trust_level": _AN}
    state.update(extra)
    return state


class TestPublishedReport:
    def test_clean_draft_is_published(self, node):
        result = node(_state(_CLEAN_DRAFT))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["formatted_output"] == _CLEAN_DRAFT

    def test_status_written_as_plain_string(self, node):
        result = node(_state(_CLEAN_DRAFT))
        # The exact type matters: the status enum is a str subclass, so an
        # isinstance() check would pass on a leaked enum member.
        assert type(result["status"]) is str  # noqa: E721

    def test_empty_draft_publishes_nothing(self, node):
        """With no draft the node publishes nothing rather than improvising."""
        result = node(_state(""))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["formatted_output"] == ""

    def test_internal_state_is_never_published_as_a_substitute(self, node):
        """A missing draft must not fall back to dumping validated state."""
        result = node(_state("", validated_data='{"portfolio_id": "PF-001"}'))
        assert result["formatted_output"] == ""

    def test_required_trust_level(self, node):
        assert node.required_trust_level is TrustLevel.ANONYMOUS


class TestDisallowedContent:
    @pytest.mark.parametrize(
        "payload",
        [
            "API key: sk-ABCDEFGHIJKLMNOPQRSTUV",
            "Token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g",
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
            "password: hunter2hunter2",
            "Taxpayer reference 123-45-6789 on file",
        ],
    )
    def test_disallowed_content_withholds_the_report(self, node, payload):
        result = node(_state(f"{_CLEAN_DRAFT}\n{payload}\n"))
        assert result["status"] == AgentStatus.ERROR.value
        assert payload not in result["formatted_output"]

    def test_ordinary_report_prose_is_not_flagged(self, node):
        """The screen must not fire on the documents this agent actually renders."""
        assert _security_gate_output(_CLEAN_DRAFT) is None

    @pytest.mark.parametrize(
        "text",
        [
            "**Reporting Period**: 2024-01-01 to 2024-12-31",
            "**Reporting Period**: 2025-Q4",
            "**Client ID**: CL-0198",
            "Total Portfolio Exposure: JPY 1,235,000",
            "Debt-to-Income Ratio | 31.4%",
        ],
    )
    def test_real_report_lines_are_not_flagged(self, text):
        assert _security_gate_output(text) is None


class TestScanOrderAroundTheGrid:
    """A government identifier must be caught, not mangled into something else.

    The grid enforcement rewrites digits. A scan placed only after it can be
    blind to a pattern the rewrite has already broken, so the scan runs before
    the snap and again afterwards. The identifier guards in the grammar close
    the same gap at source: a hyphenated identifier is not a monetary token, so
    the snap leaves it byte-identical and both scans see the same text.
    """

    @pytest.mark.parametrize("secret", ["123-45-6789", "987-65-4321"])
    def test_identifier_survives_the_grid_unchanged(self, secret):
        text = f"Reference {secret} on file"
        snapped, count = _enforce_precision(text)
        assert snapped == text
        assert count == 0

    @pytest.mark.parametrize("secret", ["123-45-6789", "987-65-4321"])
    def test_identifier_in_a_report_withholds_it(self, node, secret):
        result = node(_state(f"{_CLEAN_DRAFT}\nTaxpayer reference {secret}\n"))
        assert result["status"] == AgentStatus.ERROR.value
        assert secret not in result["formatted_output"]
        assert "6789" not in result["formatted_output"]


class TestPrecisionGridSnaps:
    """Every monetary representation lands on the published grid."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            # Marker before the value: the comma-grouped form must match as a
            # whole token, otherwise "JPY 1,234" would snap its leading "1"
            # and render "JPY 0,234".
            ("JPY 1,234", "JPY 1,000"),
            ("JPY 9999", "JPY 10,000"),
            ("JPY  9999", "JPY  10,000"),
            ("JPY\t9999", "JPY\t10,000"),
            ("USD\n9999", "USD\n10,000"),
            ("JPY-9999", "JPY-10,000"),
            ("JPY +9999", "JPY +10,000"),
            ("¥9999", "¥10,000"),
            ("￥9999", "￥10,000"),
            # Marker after the value.
            ("9999 JPY", "10,000 JPY"),
            ("9999円", "10,000円"),
            ("-9999\tEUR", "-10,000\tEUR"),
            # Form alone, at any magnitude — no currency context needed.
            ("Total 1234567 recorded", "Total 1,235,000 recorded"),
            ("Total 1,234,567 recorded", "Total 1,235,000 recorded"),
            ("Total 9,999 recorded", "Total 10,000 recorded"),
            ("Total 99999 recorded", "Total 100,000 recorded"),
            # A decimal part belongs to the amount and snaps with it.
            ("JPY 1234.56", "JPY 1,000"),
            ("JPY 1,234,567.89", "JPY 1,235,000"),
            ("Total 1234567.89 recorded", "Total 1,235,000 recorded"),
            ("sentence ends with JPY 1234.", "sentence ends with JPY 1,000."),
        ],
    )
    def test_off_grid_value_snaps(self, text, expected):
        snapped, count = _enforce_precision(text)
        assert snapped == expected
        assert count == 1

    @pytest.mark.parametrize("text", ["JPY 1,000", "JPY 1000", "1,235,000 JPY", "¥2,000", "JPY 1,000.00"])
    def test_on_grid_value_is_untouched(self, text):
        snapped, count = _enforce_precision(text)
        assert snapped == text
        assert count == 0

    def test_suffixed_decimal_does_not_backtrack_to_the_integer(self):
        # With an optional fraction — `(?:\.\d+)?` — the engine backtracks out
        # of ".56" when the trailing guard rejects "m" and re-matches "1234"
        # alone, leaving the fraction dangling: "JPY 1,000.56m". Absorption
        # must be all-or-nothing.
        snapped, count = _enforce_precision("JPY 1234.56m")
        assert snapped == "JPY 1234.56m"
        assert count == 0
        # Not an exemption: without the suffix the same amount still snaps,
        # as ONE number.
        snapped, count = _enforce_precision("JPY 1234.56")
        assert snapped == "JPY 1,000"
        assert count == 1

    def test_snap_is_audited_through_the_node(self, node):
        result = node(_state("Total Portfolio Exposure: JPY 1234567\n"))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "JPY 1,235,000" in result["formatted_output"]
        assert "1234567" not in result["formatted_output"]


class TestPrecisionGridLeavesStructureAlone:
    """Structural tokens are not monetary and must survive byte-identical."""

    @pytest.mark.parametrize(
        "text",
        [
            # The delimiter must not span a paragraph break: a three-letter
            # code ending a line would otherwise bind to the number that opens
            # the next block and rewrite the document's section numbering.
            "**Currency**: JPY\n\n3. Cash Position",
            "**Currency**: JPY\n\n2026 Outlook",
            # Caller identifiers render over [A-Za-z0-9_-]; the guards cover
            # that whole alphabet, the underscore included.
            "**Portfolio ID**: PF-2024-001",
            "**Portfolio ID**: PF_48210",
            "**Client ID**: CL-0198",
            "**Entity ID**: ENT-A42",
            "**Reporting Period**: 2025-Q4",
            "**Reporting Period**: 2024-01-01 to 2024-12-31",
            # Ordinary report figures and structural tokens.
            "Risk Score: **8.5 / 10**",
            "| Credit Score | 720 |",
            "Total Return | -3.25%",
            "horizon 90d",
            "in 2026 the requirement changes",
            "STAR 2026",
            "version v12 of the model",
            # A fraction is not a standalone digit run: without the decimal
            # point in the leading guard, "9999.99999" would have its fraction
            # read as a five-digit amount and rewritten to "9999.100,000".
            "Total Return | 9999.99999%",
            "Risk Score: **8.512345 / 10**",
            "| Debt-to-Income Ratio | 31.4% |",
            "Total Return | -3.25%",
            "ratio 0.123456",
            "version v1.2345",
            "10.20.30.40",
        ],
    )
    def test_structural_token_is_byte_identical(self, text):
        snapped, count = _enforce_precision(text)
        assert snapped == text
        assert count == 0

    def test_section_numbering_survives_a_currency_line(self, node):
        draft = "**Currency**: JPY\n\n3. Cash Position\n\nBalance: JPY 2,000\n"
        result = node(_state(draft))
        assert result["formatted_output"] == draft


class TestVerbatimCallerText:
    """The caller's own request text must not be echoed back in the report."""

    def test_verbatim_request_is_redacted(self, node):
        raw = '{"report_type": "risk_assessment", "input_data": {"portfolio_id": "PF-001"}}'
        result = node(_state(f"# Report\n\n{raw}\n", user_input=raw, validated_input=raw))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert raw not in result["formatted_output"]
        assert "[REDACTED]" in result["formatted_output"]


class TestGateShape:
    def test_no_framework_gate_override(self):
        """The document gate is a module function, not an overridden framework hook."""
        for attribute in (
            "_security_gate_input",
            "_security_gate_output",
            "_extra_security_gate_input",
            "_extra_security_gate_output",
        ):
            assert attribute not in PostProcessNode.__dict__

    def test_execute_signature(self, node):
        import inspect

        parameters = list(inspect.signature(node.execute).parameters)
        assert parameters[0] == "state"
        assert not hasattr(node, "_invoke_impl")
