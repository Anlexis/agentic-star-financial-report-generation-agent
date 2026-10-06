# FIN-C2-007 — Design Specification

## Position in the framework

| Aspect | Value |
|---|---|
| Agent class | `FinC2007Agent` (`src/graph/graph.py`) |
| L1 Base (framework base class) | `AgentBaseGraph` — direct framework inheritance |
| Pattern | Document generation — template-driven and deterministic; no model backend |
| `base_type` | `DocGenerationAgent` |
| Composition | State: flat TypedDict (`src/schemas/state.py`) · Node: `FunctionNode` subclasses with `execute(self, state, config=None) -> dict` · Graph: `register_nodes()` fills the backbone slots |

## Architecture overview

The agent is a flat pipeline: every node is a plain `FunctionNode` registered directly on the
`AgentBaseGraph` backbone. There is no inner graph and no `GraphNode` wrapper — the domain
workflow is a fixed sequence, and a sequence does not repay a second graph to compile.

### Backbone slots

| Slot | Node class | Role |
|------|-----------|------|
| `initialize` | `InitializeNode` (framework default) | Seeds session, trust level and the structured input channel |
| `pre_process` | `PreProcessNode` | External input boundary: caller trust, screening, request contract |
| `main` | `ReportGenerationNode` | Validate the payload → select the template → render the document |
| `post_process` | `PostProcessNode` | External output boundary: content scan, verbatim redaction, precision grid |
| `finalize` | `FinalizeNode` (framework default) | Builds the response metadata |

### The main slot

`ReportGenerationNode` (`src/nodes/report_generation_node.py`) runs three steps in order and
stops at the first one that reports an error, so a rejected payload never reaches the renderer:

```
DataValidationNode  ->  TemplateSelectorNode  ->  ReportDraftNode
```

Each step is an independent node with its own contract and its own unit tests. The sequencing
node threads each step's partial state update into the next step's view of state and returns the
accumulated updates as a single partial dict, so the backbone sees one ordinary node.

### Node composition detail

| Node | File | Trust level | Reads | Writes |
|------|------|-------------|-------|--------|
| `PreProcessNode` | `pre_process_node.py` | `VERIFIED_EXTERNAL` | `user_input`, `input_context` | `report_type`, `input_data`, `validated_input` |
| `ReportGenerationNode` | `report_generation_node.py` | `ANONYMOUS` | `report_type`, `input_data` | `validated_data`, `selected_template`, `report_draft` |
| `DataValidationNode` | `data_validation_node.py` | `ANONYMOUS` | `report_type`, `input_data` | `validated_data` |
| `TemplateSelectorNode` | `template_selector_node.py` | `ANONYMOUS` | `report_type` | `selected_template` |
| `ReportDraftNode` | `report_draft_node.py` | `ANONYMOUS` | `validated_data`, `selected_template` | `report_draft` |
| `PostProcessNode` | `post_process_node.py` | `ANONYMOUS` | `report_draft` | `formatted_output` |

### Data flow

```
START
  -> initialize            seeds session, trust level, input_context
  -> pre_process           screen both caller channels; assemble and check the
                           request; write report_type + input_data
  -> main                  validate the payload against the field contract;
                           load the template; render the document
  -> {route: SUCCESS -> post_process; ERROR -> finalize}
  -> post_process          content scan; verbatim redaction; precision grid;
                           re-scan; write formatted_output
  -> finalize
END
```

## Caller-data contract

A request may arrive as a JSON object in `user_input`, through the structured `input_context`
channel, or both; `input_context` values win, because that channel is explicit. Both channels are
screened for personal data and prompt-injection forms. The framework's own input gate reads
`user_input` only, so `PreProcessNode` screens the context channel itself, and it screens
unconditionally — a check that only runs because something upstream ran first is not a guarantee.

Both screens read the **serialized** form of their channel, so caller text buried inside a nested
mapping or list is screened exactly like a top-level string. A screen that walked only top-level
values would be blind to precisely where an attacker would put the payload.

The output boundary has no equivalent surface: it scans the assembled document, which is a single
string by the time it reaches the boundary.

`src/schemas/report_contract.py` is the single source of truth for the contract.

| `report_type` | Fields |
|---------------|--------|
| `risk_assessment` | `portfolio_id` (identifier), `risk_score` (0–10), `total_exposure` (0–1e15, monetary), `currency` (`JPY`/`USD`/`EUR`/`GBP`) |
| `credit_summary` | `client_id` (identifier), `credit_score` (300–850), `dti_ratio` (0–100), `credit_utilization` (0–100) |
| `portfolio_performance` | `portfolio_id` (identifier), `total_return` (−1000–10000), `period_start` (ISO date), `period_end` (ISO date) |
| `compliance_status` | `entity_id`, `reporting_period`, `regulation_name` (identifiers), `compliance_status` (`COMPLIANT`/`NON_COMPLIANT`/`UNDER_REVIEW`) |

**Numbers.** Every caller number goes through `finite_in_range()`: booleans are rejected even
though Python treats them as integers, NaN and ±Infinity are rejected explicitly, and every field
carries an upper as well as a lower bound. A bare range comparison would let NaN through — every
ordering comparison against NaN is False — and it would then suppress whichever decision it takes
part in. Integers that are too large to convert to float are out of range by definition.

**Identifiers.** Identifiers render verbatim into the document, so they are restricted to
1–32 characters from `[A-Za-z0-9_-]`: no whitespace, no Markdown or HTML structure, no
substitution markers. They must also contain at least one letter — see the precision grid below
for why.

**Structural limits.** At most 32 entries in a payload; 64 KB on the request body and on the
serialized context channel inside the graph; 256 KB on the context channel at the HTTP adapter.

**Failure output.** Every rejection names the offending field and never repeats the value the
caller sent.

## Output boundary

`PostProcessNode` applies three independent layers, and their order is load-bearing.

1. **Disallowed-content scan.** API keys, JWTs, Bearer tokens, credential assignments and
   government identifier numbers anywhere in the document withhold the report entirely.
2. **Verbatim caller-text redaction.** The document is assembled from validated fields and
   template prose, so the caller's raw request text appearing verbatim is a leak, not a feature.
3. **Monetary precision grid.** Every monetary-form token is snapped onto the published grid of
   1,000 currency units, with an audit event per snap.

The scan runs **before** the grid enforcement and again after it. Grid enforcement rewrites
digits, so a scan placed only after it can be blind to a pattern the rewrite has already broken —
`123-45-6789` mangled into something else is no longer recognisable as an identifier number. The
identifier guards described below close the same gap at source, and the re-scan proves the snap
introduced nothing new.

### The precision grid

Monetary values are identified by **form** and by **currency context**, never by magnitude:

- form: comma-grouped numbers, and unformatted runs of five or more digits;
- context: a bare one-to-four digit number next to a currency marker — a standalone three-letter
  uppercase code or a currency symbol, before or after the value, attached or separated, signed
  or unsigned.

The grammar matches marker, delimiter and value as groups rather than using lookbehinds, because
Python lookbehinds are fixed-width and would silently cap a "separated" match at one character.
The marker-to-value delimiter is horizontal whitespace plus at most one newline — never a
paragraph break. A delimiter that spans blank lines would let a three-letter code ending one line
bind to the number opening the next block, so `Currency: JPY` above `3. Cash Position` would
renumber the section.

**Decimal parts.** A value alternative absorbs an optional decimal part, and the decimal point
also joins the leading guard. Without both, the fraction of `9999.99999` is a standalone
five-digit run: the guards would admit it and a rendered percentage would be rewritten to
`9999.100,000`. The decimal point stays out of the *trailing* guard, so an amount ending a
sentence still snaps.

**Identifier guards.** The whole grammar is wrapped in single-character guards over
`[A-Za-z0-9_-]`, the alphabet caller identifiers render in — the underscore included, because a
bare digit run following one would otherwise snap. A monetary token never begins or ends inside
an identifier, so `PF-2024-001` and `PF_48210` come back byte-identical. The guards are also why
identifiers must carry at least one letter: a purely numeric reference has no neighbouring
identifier character to protect it, and would be rewritten in the caller's own report. It is
refused at input instead.

## State definition

`src/schemas/state.py` — a flat TypedDict extending `AgentState`.

| Field | Type | Purpose | Written by |
|-------|------|---------|-----------|
| `validated_input` | `Optional[str]` | The screened raw request | `PreProcessNode` |
| `report_type` | `Optional[str]` | The requested report type | `PreProcessNode` |
| `input_data` | `Optional[str]` | JSON string of the caller's data | `PreProcessNode` |
| `validated_data` | `Optional[str]` | JSON string of contract fields only | `DataValidationNode` |
| `selected_template` | `Optional[str]` | Template content | `TemplateSelectorNode` |
| `report_draft` | `Optional[str]` | The rendered document | `ReportDraftNode` |
| `formatted_output` | `Optional[str]` | The published report | `PostProcessNode` |
| `error_message` | `Optional[str]` | Error detail | any node |

State constraints: flat TypedDict only, no rich objects; no credentials; structured data stored
as JSON strings for checkpoint safety; `formatted_output` explicitly declared, because the graph's
`get_output()` reads it and an undeclared key is dropped.

## Document templates

Templates live in `config/templates/{report_type}.md` and carry `${FIELD_NAME}` markers, filled
from validated fields of the same name (upper-cased). Markers with no value render as `[TBD]`, so
an incomplete template is visibly incomplete rather than silently wrong; the shipped templates
leave none. Narrative sections receive deterministic prose from `ReportDraftNode` — that map is
the seam where a model backend would be attached. `report_type` is re-checked against the
supported set before it is used to build a template path.

## Runtime configuration

`config/agent.yaml` is the flat manifest. Runtime parameters live in `config/config.yaml` and are
read by `runtime_config()` in `src/graph/graph.py`, which the standalone server passes to the
constructor — so a registry-loaded agent and a standalone one see identical values. Declared
values are bounds-checked before they are forwarded: a non-finite retry bound would compare False
against every retry count and silently disable retries.

## Import isolation

The template imports `framework.*`, `shared.utils.audit_logger`, `shared.secrets` and `src.*`
only. It never reaches past the framework into the platform SDK; PB-4 enforces this with an AST
scan over `src/`.

## Security design

| Concern | Implementation | Where |
|---|---|---|
| Caller trust | `required_trust_level = VERIFIED_EXTERNAL` on the boundary node; bearer-token promotion at the HTTP adapter | `PreProcessNode`, `src/api/server.py` |
| Input screening | Personal-data and injection screens on both caller channels, run unconditionally | `PreProcessNode` |
| Input contract | Finite bounded numbers, inert identifiers, real dates, closed enumerations, entry and size caps | `src/schemas/report_contract.py` |
| Output boundary | Content scan → verbatim redaction → precision grid → re-scan | `PostProcessNode` |
| Audit | `emit_trace_event()` on every node's success path, on every validation failure, and on every output-boundary action | all nodes |
| Credentials | None in state, none in code; secrets are reached through the framework's provider | all nodes |

The domain output gate is a module-level function called from `execute()`, not an instance
method: the framework's own gate methods are final and may only be extended through the
documented hooks, and this template overrides neither.
