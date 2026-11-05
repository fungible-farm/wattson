# 0033 — Audit an engineer's study workbook: every number traced, re-derived or flagged

- **Status:** **deferred** (user, 2026-10-23: skip Excel; don't reinvent the wheel). Prototype measured 2026-10-23 (scratchpad, not committed). **Merit: above
  0030–0032,** because it needs no blocked data.
- **Affects:**
  - PC-REQ-004 provenance, applied to the engineer's own artefacts;
  - [0026](0026-walkable-path-namespace-ls-get.md) (cells addressable as `/xlsx/<file>/<sheet>/<cell>`);
  - G6 investigations.

## Why
Engineers keep study inputs, tool exports, hand calculations and deliverables in Excel. Spreadsheet
risk research (EuSpRIG) attributes its error figures to the original studies; these are quoted
from EuSpRIG's research page, not measured here:
- most spreadsheets contain errors (Panko 2006; Pryor 2004);
- about 50% of operational models in large businesses have material defects (Powell, Baker &
  Lawson 2007; Croll 2008).

Thorne & Collins ([arXiv:2609.31815](https://arxiv.org/abs/2609.31815), v1, 2026-09-25) classify
121 incidents in the EuSpRIG horror-story archive into 12 categories:
- formula errors, data-entry slips and data handling account for 71;
- hidden or embedded data is the category with the clearest rise.

It is a corpus of *reported* incidents, not an error rate. A study workbook fails in exactly the
way the provenance rule guards against: a number with no source, or one that no longer
reproduces.

## Prototype (measured)
Built a workbook from 10 real snemSA N-1 solves (`run_study_trial`) plus a per-unit sheet, and
seeded 6 errors. A scanner (openpyxl to read; `formulas`, EUPL-1.1+, to recalculate; no LibreOffice,
by user decision) found **6 of 6, each by at least one independent check, with 0 findings off the
seeded cells** (12 findings, 5.6 s including the re-solves):

| Seeded error | Caught by |
|---|---|
| S1 copy-paste drift (line 5's violation count copied from line 2: 36 vs 39) | re-derivation (solver says 39) |
| S2 unit slip (a pu voltage entered as kV) | unit plausibility ("1.0941 pu at 275 kV") + re-derivation |
| S3 a flag typed over its formula | column formula pattern + flag recomputed from the solver |
| S4 a limit hard-coded in one formula (0.9, not the 0.95 limit cell) | pattern break + literal in a formula + flag recomputed |
| S5 a hidden row holding data | hidden-data check |
| S6 the wrong MVA base in a per-unit formula (10 vs the case's 100) | pattern break + literal + base check against the case |

**A finding about the test itself.** The first S1 (a minimum voltage copied from the neighbouring
outage) was correctly *not* flagged. On snemSA, lines 4 and 5 give minimum voltages 1.8e-7 pu
apart, below the tolerance and far below engineering significance (backlog 0023). Seeded errors
must be material for the network they're seeded into.

## Limits of the prototype (why it is not yet evidence of an agent skill)
- **Circular.** I seeded the errors and wrote the checks, so 6/6 shows that the checks work, not
  that they find unknown errors. It needs errors seeded by someone else, and real engineer
  workbooks.
- **The layout was given to it.** Its column-to-quantity map (`"Min V (pu)" → min_bus_voltage_pu`,
  `"Line out"` → the contingency) is hard-coded. Mapping an arbitrary workbook's headers, units and
  row meanings onto solver quantities is the hard, open-ended part, and that is where a model
  earns its place. Every mapping it proposes must then be checked by the deterministic layers.
- **Coverage.** It knows N-1 voltage and loading, and per-unit bases. Fault levels (0028), ratings,
  impedance from conductor data, and multi-sheet links come next.

## Next
1. **A lab** with the generator, the scanner and a held-out seed set (errors seeded by a separate
   process or person). Score it as recall and precision per error class, against a fixture.
2. **Model-proposed layout mapping:** headers and units → quantities and contingencies. The
   deterministic layers check each mapping, and each finding cites a cell and a solver result.
3. **A report** an engineer would accept: each cell's source (tool result, cited input, formula,
   unsourced), with re-derived values and their solver citations.

## openpyxl-mcp-server (evaluated 2026-10-23)
[jonemo/openpyxl-mcp-server](https://github.com/jonemo/openpyxl-mcp-server) (MIT, commit 6fb0863) is a
reference for the workbook tools, not something to load as-is. Defects were reproduced on a clean
workbook and filed upstream:
- [#10](https://github.com/jonemo/openpyxl-mcp-server/issues/10) empty tool descriptions;
- [#11](https://github.com/jonemo/openpyxl-mcp-server/issues/11) ranges sorted as strings (`A9:A11` is empty);
- [#12](https://github.com/jonemo/openpyxl-mcp-server/issues/12) values wrapped in set braces;
- [#13](https://github.com/jonemo/openpyxl-mcp-server/issues/13) the pinned `mcp` 1.4.1 fails with current pydantic;
- [#14](https://github.com/jonemo/openpyxl-mcp-server/issues/14) no formula results, no hidden-row or hidden-sheet state.

It also opens any filesystem path, while PowerCoder's tools take identifiers only. Plan: a workbook
tool group in PowerCoder's own MCP server with workbooks by id, formula plus recalculated value
(`formulas`), and hidden state. The harness connects each attempt to one stdio server.

## excel-mcp-server (evaluated 2026-10-23): use this one
[haris-musa/excel-mcp-server](https://github.com/haris-musa/excel-mcp-server) v2.2.2 (MIT, released
2026-10-21, `mcp` 2.x). I read its source before running it: no subprocess or shell calls, and its
`eval` is its own formula engine walking a parsed expression. Probed read-only on the seeded study
workbook:
- **8 read-only tools, with descriptions:** `describe_workbook`, `list_workbooks`,
  `export_workbook`, `describe_sheet`, `read_range`, `find_cells`, `trace_formula`, `read_vba`.
- **Formula results:** `read_range(mode="values")` returns them at full precision
  (`0.9070106356318516`; the `"ok"`/`"LOW"` flags evaluated), using a built-in calculator.
  Results Excel saved are preferred. A formula it cannot reproduce exactly is returned as null
  and listed in `uncalculated` with the reason, "never guessed" (its README; checked against
  more than 2,500 formulas recorded from Excel). That suits the provenance rule.
- **Hidden rows** are reported by `describe_sheet` (`"hidden_rows":["11:11"]`).
- **`trace_formula` gives provenance.** The seeded H5 has only D5 as a precedent, where its
  column's flags depend on D*n* and the limit cell K1, which exposes the hard-coded 0.9.
- **Confined:** `--allow-dir`, resolving symlinks, refused `/etc/passwd`. `--read-only` registers
  only read tools; `--tools` limits which tool areas are advertised (core is 8,900 characters of
  descriptions and schemas, per its README).
- **Compatible:** the repo's `mcp` 1.12.2 client works with it as a separate stdio process
  (protocol 2025-06-18). Its own dependencies (`mcp` 2.x, pillow, starlette, uvicorn) stay in its
  own environment.

**Deferred (user, 2026-10-23): not in the harness.** Its core tools alone advertise 8,900 characters
of descriptions and schemas on every request, about twice PowerCoder's whole tool block (about 4.2k
characters), and that isn't necessary. It stays the reference for behaviour worth copying
(full-precision formula results, a calculator that never guesses, hidden rows, precedents, folder
confinement). The workbook layer is instead a small tool group in PowerCoder's own server:
openpyxl to read, `formulas` to recalculate, descriptions kept short and measured. The plan
originally considered:

Use excel-mcp-server (read-only, confined to a workbook directory)
for the workbook layer instead of reimplementing it. PowerCoder's own server keeps the
power-engineering parts: quantity and unit mapping, plausibility checks, and solver
re-derivation. The evaluation harness connects each attempt to one stdio server
(`tool_probe`), so using both needs a small two-session multiplexer (tool names don't clash).
That changes the tool set, so it needs its own run root. Interactive use (Claude Code, pi) can
register it in `.mcp.json` directly.
