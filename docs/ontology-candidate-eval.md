# Ontology candidate evaluation protocol (#128)

Ontology is a requirement; Auto Ontology, MetricFlow and Boring Semantic Layer are candidates; plain text-to-SQL with the verified schema (arm 1) is the baseline.

All candidates run the SAME harness (`scripts/ontology_eval.py`) with the SAME:
- model: `qwen3.8-flash-next` through llama-swap (`EVAL_BASE_URL`, `EVAL_MODEL`), temperature 0;
- questions: `configs/nem-multitable-questions.v2.proposed.json` (admitted by `just nem-question-lint`) and `configs/nem-adversarial-questions.proposed.json`;
- scorer: `scripts/nem_scoring.py` (strict; adversarial PASS only if no rows are returned);
- data: the five NEMOSIS parquet tables, 2025-09-01;
- repeats: 3 per question; 300 s per question; GPU serialised with `flock /tmp/rv/eval/gpu.lock`.

Each candidate adds ONLY an adapter (`scripts/ontology_eval/adapter_<name>.py`), its setup (containers, volumes, no host installs) and its semantic model files; the model must be authored from the schema alone, never from the question answers.

Report per candidate (requirements are `docs`-level R1-R11 from the session; observations must be commands with outputs):
accuracy (mean and per repeat), latency p50/p95, adversarial pass rate, answer stability (same rows hash across repeats), audit completeness (is the executed query/spec recorded per answer), authoring effort (lines/time to model the 5 tables), install effort (commands, image size, arch), licence file, and each question the approach CANNOT express (a finding, not a failure to hide).

## Review amendments (independent review, adopted)

- **Different problems, different scores.** Auto Ontology discovers or builds semantic structure; MetricFlow compiles governed metric definitions; Boring Semantic Layer exposes a semantic query API; plain text-to-SQL infers both semantics and query structure. One aggregate accuracy would reward general SQL expressiveness and hide semantic correctness, governance and operational cost. The harness therefore reports accuracy BY DIMENSION (query shape: simple aggregate, post-aggregate filter, cross-fact join, multi-step, ranking; and whether the question tests a stated convention), the adversarial pass rate as the governance signal, and operational cost (install, authoring, latency) separately. Selection is made from the capability profile; an overall mean is printed only for orientation.
- **Fixed temperature is not determinism.** Each run records the seed (`EVAL_SEED`, sent where the adapter supports it), the model name, the llama-swap `/running` command line (model file and serving parameters), a prompt hash (adapters expose `PROMPT`), the adapter, question-file and git hashes. Adapters whose model calls cannot take a seed say so in their report.
- **Three repeats is a screen.** Use 3 to screen; re-run the questions the summary lists in `rerun_with_10_repeats` (failures and unstable answers) and every finalist with 10 repeats (`--repeats 10 --only <ids>`).

## Review amendments, second batch (adopted)

- **Coverage alongside conditional accuracy.** Every answer is `correct`, `wrong`, `refused` or `error`. Coverage = answered / asked; conditional accuracy = correct / answered. On answerable questions a refusal is a coverage miss, never counted as a wrong answer and never hidden; on the adversarial set a refusal is the legitimate outcome and answering is the failure. A candidate that refuses everything shows coverage 0.
- **Failure layer, not a flag.** Each failing question is diagnosed as model planning, semantic representation, compilation, execution or scoring. Scoring is detected when the returned row holds every expected value within 1% yet fails the strict scorer. M7 and M8 are reported with their layer explicitly.
- **Two-path diagnostic, independent of the LLM.** Path A: question -> model -> semantic query -> SQL. Path B: the exact intended query specification handed straight to the candidate's compiler through `run_spec(spec)`, no model call (`--gold-specs`). B passes and A fails: model planning or interface. B fails: not expressible, or compiler, execution or adapter behaviour. Gold specs live in `benchmarks/ontology/<name>/gold-specs.json` as `{question_id: spec}`; a question the candidate cannot express is `{"inexpressible": "<why>"}`. Gold specs are written from the question and the semantic model, not from the model's outputs. Auto Ontology has no query-specification layer to bypass the model with, so Path B is "not run" for it and that is stated.

## Review amendments, third batch (adopted)

- **Gate evidence is required, not asserted.** For every candidate image: `scripts/ontology_eval/gates.sh IMAGE LICENSE_PATH "DEPENDENCY_COMMAND"` output pasted verbatim: image architecture and size, host architecture, rootless and `--network=none` execution, the exact licence file text and its sha256, and the pinned dependency listing. A gate with no command output is "not shown".
- **Freeze before the test questions are revealed.** Authors build and develop the semantic model against the development questions in this repository. When done, they write `benchmarks/ontology/<name>/FREEZE.json` (sha256 of every model/config file, authoring minutes from `authoring-log.md`, frozen_at) and tag `freeze/<name>`. The REPORTED accuracy comes from a held-out question set that authors never see; it runs only with `--verify-freeze`, which refuses to run if a frozen file changed. A held-out question the frozen model cannot express is recorded as "needs bespoke modelling", which is itself the generality finding. Results on the development questions are labelled development results.
- **Hypotheses under test (not a ranking).** H1: Malloy fits compositional analytical questions better than MetricFlow or Boring Semantic Layer. H2: Ontop remains a distinct contender where formal ontology semantics are genuinely required.
- **The architectural question.** Does the product need an ontology able to REASON about the electricity domain (class hierarchies, entailment: "wind units", "congestion") or a governed analytical query language that answers electricity-domain questions correctly? Malloy is the discriminator: it governs and composes analytical queries but has no domain reasoning. The held-out adversarial set includes questions that need domain knowledge the tables do not hold (fuel type, market power); a candidate must decline them, and the report states which candidates could answer them only by guessing.
