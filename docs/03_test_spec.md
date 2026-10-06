# FIN-C2-007 — Test Specification

## Scope

Unit tests exercise each node against its own contract; boundary tests exercise the shipped
public path end to end. Both run without a platform connection.

Nodes are invoked through `BaseNode.__call__` (`node(state)`) rather than `execute()` directly,
so the framework's trust and input gates run on every unit invocation. Where a test proves a
guarantee the **template** owns — the input screens — it calls `execute()` directly, with no
framework wrapper in front of it: a refusal that only happens because something upstream refused
first is not a guarantee wherever that upstream check is absent.

Run the suite with:

```bash
python -m pytest tests/ -v
```

---

## Framework compliance (`tests/unit/test_framework_compliance_tc06_tc07.py`)

| TC | Test | Expected |
|----|------|----------|
| TC-06 | The default input gate cannot be replaced | Class definition raises `TypeError` |
| TC-07 | The default output gate cannot be replaced | Class definition raises `TypeError` |

---

## Unit tests

### `PreProcessNode` — `tests/unit/test_pre_process_node.py`

| Group | Covers |
|-------|--------|
| Accepted requests | JSON body; structured channel alone; structured channel overriding the body; all four report types; report-type normalisation; status written as a plain string; declared trust level |
| Rejected requests | Unusable body; missing/blank/non-string/unsupported `report_type`; missing or non-object `input_data`; non-object context channel; body and context size caps; payload entry cap; the rejected report type is not echoed back |
| Screens on every channel | Four injection forms and four personal-data forms, refused in the JSON body **and** in the structured channel, asserted behaviourally (error status, nothing carried forward); the masked-content marker; text buried in a nested mapping or list is refused, with a clean-text control of the same shape proving the probe itself works; identifiers such as `PF-0120-3456` and `IDX-0999-1234` are **not** mistaken for telephone numbers; ordinary financial wording is not refused |
| Trust boundary | An ANONYMOUS caller is denied; a VERIFIED_EXTERNAL caller is admitted; `execute(state)` signature |

### `DataValidationNode` — `tests/unit/test_data_validation_node.py`

| Group | Covers |
|-------|--------|
| Valid payloads | Every report type; integers stay integers; unknown caller keys are dropped; range boundaries are inclusive |
| Non-finite numbers | A parametrized matrix — `nan`, `inf`, `-inf`, the strings `"NaN"`/`"Infinity"`/`"-Infinity"`, `True`, a numeric string, `None`, a list — applied to **every** numeric field in the contract; absurd magnitude (`10**60`) per field; negative amount; the rejected value is not echoed |
| Identifier contract | Inert forms accepted (`PF-2024-001`, `PF_48210`, 32 characters); whitespace, table pipes, headings, markup, substitution markers, newlines, over-length, empty and non-string rejected; a purely numeric identifier rejected |
| Dates and enumerations | Impossible and mis-formatted dates; supported and unsupported currencies; the three compliance statuses |
| Structural limits | Missing required field; payload entry cap; missing/​non-JSON/​non-object `input_data`; unknown report type; declared trust level |

### `TemplateSelectorNode` — `tests/unit/test_template_selector_node.py`

Every report type resolves to a template carrying `${...}` markers; the risk template's content;
missing report type rejected; an unsupported or traversal-shaped report type is rejected before
it reaches the filesystem; declared trust level and `execute(state)` signature.

### `ReportDraftNode` — `tests/unit/test_report_draft_node.py`

Marker substitution; unknown markers become `[TBD]`; narrative sections filled; empty template;
monetary amounts rendered on the published grid (`1234567` → `1,235,000`, `999` → `1,000`);
the precision note present when a monetary field is rendered and absent when none is; non-monetary
numbers rendered unchanged; missing `validated_data`, missing template and non-JSON data rejected.

### `ReportGenerationNode` — `tests/unit/test_report_generation_node.py`

All three steps run and produce a document; the amount is on the grid; every report type produces
a document; a rejected payload short-circuits **before** the renderer, writing neither
`validated_data` nor `report_draft`; declared trust level and `execute(state)` signature.

### `PostProcessNode` — `tests/unit/test_post_process_node.py`

| Group | Covers |
|-------|--------|
| Published report | A clean draft is published unchanged; an empty draft publishes nothing; internal state is never published as a substitute for a missing draft |
| Disallowed content | API key, JWT, Bearer token, credential assignment and government identifier each withhold the report; ordinary report prose and real report lines are **not** flagged |
| Scan order around the grid | A government identifier survives grid enforcement byte-identical, and a report containing one is withheld with no fragment of it in the output |
| Grid snaps | 21 forms: marker-before (including the comma-grouped `JPY 1,234` case), marker-after, multi-space, tab, newline, signed, fullwidth and symbol markers, form-alone at any magnitude, and amounts carrying a decimal part; on-grid values byte-identical |
| Grid leaves structure alone | 22 tokens: a currency line above a numbered heading; caller identifiers over `[A-Za-z0-9_-]`; dates and quarters; scores, ratios, counts, horizons, years and embedded acronyms; long decimal fractions, percentages, version strings and dotted numbers |
| Verbatim caller text | The caller's raw request embedded in the draft is redacted |
| Gate shape | No framework gate method is overridden; `execute(state)` signature |

---

## Boundary tests

### PB-4 — import isolation (`tests/proof_of_boundary/test_import_isolation.py`)

AST scan over `src/`: no import reaches past the framework into the platform SDK.

### PB-2 / PB-5 — state safety (`tests/proof_of_boundary/test_state_safety.py`)

AST scan over `src/schemas/state.py`: no credential-shaped field names, no prohibited type
annotations.

### PB-6 — backbone invoke order (`tests/proof_of_boundary/test_pb_invoke_order.py`)

Valid payload: `{"report_type": "risk_assessment", "input_data": {"portfolio_id": "PF-PB6-001",
"risk_score": 6.0, "total_exposure": 2000000, "currency": "JPY"}}` — the same payload
`deploy/invoke_payload.json` carries.

| PB | Test | Expected |
|----|------|----------|
| PB-6a | `FinC2007Agent().invoke()` with a VERIFIED_EXTERNAL caller | `node_history` includes `ReportGenerationNode` |
| PB-6b | Ordering | `PreProcessNode` precedes the main slot node |
| PB-6c | Invalid payload | Status is `error`; `post_process` is skipped |

A VERIFIED_EXTERNAL caller is required: an INTERNAL caller outranks every gate and would give a
false green.

### PB-7 — HITL interrupt propagation (`tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py`)

Not enabled for this template — a flat pipeline with no `GraphNode` declaring `propagate_hitl`
and no cross-boundary interrupt checkpoint. Ships as a real, importable module that skips with a
stated reason.

### End to end through `/invoke` (`tests/proof_of_boundary/test_invoke_e2e.py`)

Driven through the application's real ASGI interface with bearer-token authentication.

| Group | Covers |
|-------|--------|
| Real report from caller data | The report is built from the caller's payload; the structured channel alone produces one; every report type is reachable and leaves no unresolved marker or gap |
| Published precision | Amounts appear on the grid and the full-precision form appears nowhere; the report states its precision; caller identifiers come back byte-identical |
| Rejections | Non-finite numbers arriving as bare `NaN`/`Infinity`/`-Infinity` in raw JSON, per numeric field; absurd magnitude; negative amount; boolean; purely numeric identifier; structure-bearing identifier; unsupported currency; unknown or missing report type; unparseable body; injection attempt; oversized structured channel rejected at the adapter with 413 |
| Caller authentication | An unauthenticated caller and a wrong token both get an error and no report |

---

## Coverage summary

| Guarantee | Where it is proven |
|---|---|
| Caller trust is enforced at the boundary | `test_pre_process_node.py::TestTrustBoundary`, `test_invoke_e2e.py::TestCallerAuthentication` |
| Screens hold without a framework wrapper, and do not fire on real text | `test_pre_process_node.py::TestScreensOnEveryChannel` |
| Every caller number is finite and bounded | `test_data_validation_node.py::TestNonFiniteNumbers`, `test_invoke_e2e.py::TestRejectionsThroughTheEndpoint` |
| Caller strings cannot carry document structure | `test_data_validation_node.py::TestIdentifierContract` |
| The published report holds its precision invariant | `test_post_process_node.py::TestPrecisionGridSnaps`, `TestPrecisionGridLeavesStructureAlone`, `test_invoke_e2e.py::TestPublishedPrecisionEndToEnd` |
| Content screening is not defeated by grid enforcement | `test_post_process_node.py::TestScanOrderAroundTheGrid` |
| The public path does real work | `test_invoke_e2e.py::TestRealReportFromCallerData` |
