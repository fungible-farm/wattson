# 0013 — Idea: a sensitivity/Newton tool with certificates (narrow numerics)

- **Status:** absorbed into [0016](0016-g2-verified-engineering-mathematics.md) (PRD-0010 G2: sensitivities with certificates); its Newton-vs-bisection experiment is 0017's first step. Review idea 3: merit 3 in its narrow form, 2 for the full one-interface code generator, which is dropped for now ([review](reviews/2026-10-23-r6-merit-review.md)).

## Idea
LLMs can read mathematics but not compute it reliably, so they need a calculator-like co-processor. Add one MCP tool that returns the sensitivity d(metric)/d(scale) from pandapower, plus a linear solve. Each result carries a certificate: residual, condition number and convergence status. Fit tasks can then take Newton steps instead of bisection, and the agent reports the certificate rather than its own belief.

## Cheapest decisive experiment
On the existing fit tasks, count tool calls to reach tolerance with the scripted Newton policy against scripted bisection. If Newton doesn't save calls, the idea has no payoff here.

## Dropped
- **PCA to "destructure decisions":** review idea 4, merit 1. PCA explains variance across many trials; it doesn't verify a computation, and the study has no decision corpus for it to work on. Revisit only once 0008's multi-step tasks exist.
- **One interface definition generating MCP schemas, grammar and validation:** vLLM already builds the grammar from the served schemas per request (0011), so nothing is left for it to generate.
