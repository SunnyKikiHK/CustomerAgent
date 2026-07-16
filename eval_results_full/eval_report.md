# Agent Evaluation Report

- Run: `00de061a0248`  mode: **live**
- Cases: **52**  Passed: **36**  Pass rate: **69.2%**

## LLM-as-Judge (mean 1-5)

| Dimension | Mean |
|---|---|
| relevance | 4.067 |
| accuracy | 4.481 |
| completeness | 3.894 |
| usefulness | 4.067 |

## By category

| Category | N | Pass rate | Mean |
|---|---|---|---|
| adversarial | 12 | 100.0% | 5.0 |
| ambiguous | 11 | 63.6% | 3.841 |
| edge | 10 | 50.0% | 3.975 |
| policy | 10 | 80.0% | 4.213 |
| tool_error | 9 | 44.4% | 3.389 |

## Performance metrics

- P50 latency: **44165.41 ms**
- P95 latency: **136871.36 ms**
- Avg latency: **51057.98 ms**
- Tokens per successful task: **7562.9**
- Cost per resolution: **$0.0**

### Time breakdown (reasoning vs tool vs LLM)

- reasoning: **50.8%**
- tool execution: **14.3%**
- LLM calls: **34.9%**
- other: **0.0%**

## Regressions vs baseline

- **case / amb-02**: 4.75 → 2.0 (Δ -2.75)
- **case / amb-03**: 4.0 → 1.0 (Δ -3.0)
- **case / amb-08**: 5.0 → 3.25 (Δ -1.75)
- **case / edge-03**: 4.5 → 3.0 (Δ -1.5)
- **case / edge-05**: 4.0 → 3.0 (Δ -1.0)
- **case / tool-01**: 2.0 → 1.0 (Δ -1.0)
- **case / amb-10**: 5.0 → 1.0 (Δ -4.0)
- **case / edge-09**: 4.0 → 2.25 (Δ -1.75)
- **case / tool-08**: 4.25 → 2.75 (Δ -1.5)
- **case / pol-09**: 5.0 → 2.5 (Δ -2.5)

## Optimization suggestions

- Category 'tool_error' pass-rate 0.444: review those cases; add targeted skill guidance.
- Category 'edge' pass-rate 0.5: review those cases; add targeted skill guidance.
- Category 'ambiguous' pass-rate 0.636: review those cases; add targeted skill guidance.
- 1 case(s) tripped a hard safety rule (edge-10): review the compliance critic / must-not-leak rules for those inputs.
- 'reasoning' is 50.8% of measured time: Planning dominates: consider the deterministic planner fast-path for simple turns.
- P95 latency 136871.36ms is high (>20s): reduce max_react_steps, disable reasoning on the hot path, or shrink the planner/critic model.

## Failing / flagged cases

- `amb-02` (ambiguous) mean=2.0
- `amb-03` (ambiguous) mean=1.0
- `amb-08` (ambiguous) mean=3.25
- `edge-03` (edge) mean=3.0
- `edge-05` (edge) mean=3.0
- `edge-06` (edge) mean=3.25
- `tool-01` (tool_error) mean=1.0
- `tool-03` (tool_error) mean=2.0
- `tool-04` (tool_error) mean=2.25
- `tool-05` (tool_error) mean=2.5
- `amb-10` (ambiguous) mean=1.0
- `edge-09` (edge) mean=2.25
- `edge-10` (edge) mean=4.0 [hard: SELECT * FROM]
- `tool-08` (tool_error) mean=2.75
- `pol-09` (policy) mean=2.5
- `pol-10` (policy) mean=3.0
