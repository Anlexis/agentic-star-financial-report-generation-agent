# Financial Report Generation Agent

AI agent for generating financial reports, built with Agentic Star.

> **Category**: Cat 2 (domain-specific pipeline)
> **Industry**: Finance
> **Template ID**: FIN-C2-007

## Overview

Generates structured financial reports — risk assessment, credit summary, portfolio performance
and compliance status — from financial data supplied by the caller. Each request is checked
against an explicit contract before anything is rendered: every number must be finite and inside
a declared range, every identifier must match an inert character set, dates must be real calendar
dates, and enumerated fields must be one of their declared values. Validated values are then
merged into a Markdown document template, and the finished report passes an output boundary that
withholds it if it contains credential-like or personal-identifier content and places every
monetary figure on a published precision grid.

The pipeline is deterministic — no model backend is wired in — so the same request always
produces the same document. The narrative sections are the seam where a model would be attached.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent fails at graph
compile / start-up preflight rather than starting in a partially working state. This is
intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Making a request

`POST /invoke` accepts the report request as a JSON body in `input`, through the structured
`input_context` channel, or both — values in `input_context` win.

```json
{
  "input": "{\"report_type\": \"risk_assessment\", \"input_data\": {\"portfolio_id\": \"PF-2024-001\", \"risk_score\": 8.5, \"total_exposure\": 1234567, \"currency\": \"JPY\"}}"
}
```

| `report_type` | Required fields |
|---|---|
| `risk_assessment` | `portfolio_id`, `risk_score` (0–10), `total_exposure` (0–1e15), `currency` |
| `credit_summary` | `client_id`, `credit_score` (300–850), `dti_ratio` (0–100), `credit_utilization` (0–100) |
| `portfolio_performance` | `portfolio_id`, `total_return` (−1000–10000), `period_start`, `period_end` |
| `compliance_status` | `entity_id`, `reporting_period`, `regulation_name`, `compliance_status` |

Identifier fields accept 1–32 characters from `[A-Za-z0-9_-]` and must contain at least one
letter. Dates are ISO calendar dates (`YYYY-MM-DD`). Anything outside the contract is refused,
with an error that names the field and never repeats the value.

Monetary amounts are published on a grid of 1,000 currency units: the report renders them there
and the output boundary independently enforces it, so a full-precision balance cannot reach the
document through a template edit.

## Project Structure

```
src/nodes/      the pipeline: input boundary, report generation, output boundary
src/schemas/    the agent state and the caller-data contract
src/graph/      the agent graph and its runtime configuration
src/api/        the HTTP entry point
src/examples/   annotated walkthroughs of the framework patterns
tests/          unit and boundary tests, including end-to-end through /invoke
config/         the agent manifest, runtime parameters and report templates
docs/           design specification and test specification
```

See `docs/02_design.md` for the design and `docs/03_test_spec.md` for the test specification.

## Customising

1. Edit the Markdown templates in `config/templates/` — each `${FIELD_NAME}` marker is filled
   from a validated field of the same name.
2. Adjust the field contracts in `src/schemas/report_contract.py`: report types, required fields,
   numeric ranges and the identifier alphabet all live there.
3. Adjust `config/config.yaml` for your own runtime parameters.
4. Review the output boundary in `src/nodes/post_process_node.py` — the disallowed-content scan
   and the monetary precision grid are the two guarantees the published report makes.
5. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
