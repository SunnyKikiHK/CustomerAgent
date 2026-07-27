# Agent Evaluation Report

- Run: `4e9d019c26d4`  mode: **live**
- Cases: **3**  Passed: **0**  Pass rate: **0.0%**

## LLM-as-Judge (mean 1-5)

| Dimension | Mean |
|---|---|
| relevance | 3.167 |
| accuracy | 4.0 |
| completeness | 2.333 |
| usefulness | 2.63 |

## By category

| Category | N | Pass rate | Mean |
|---|---|---|---|
| ambiguous | 3 | 0.0% | 3.032 |

## Performance metrics

- P50 latency: **181248.33 ms**
- P95 latency: **192475.68 ms**
- Avg latency: **183966.53 ms**
- Tokens per successful task: **0.0**
- Total provider LLM calls: **16**
- Critic invocation rate: **100.0%** 
(3 runs / 0 skips)
- Cost per resolution: **$0.0**

### Time breakdown (application orchestration vs provider LLM)

- application/orchestration: **27.1%**
- tool execution: **0.0%**
- provider LLM calls (includes provider thinking when enabled): **72.9%**
- other: **0.0%**

## Regressions vs baseline

- **case / amb-01**: 5.0 → 2.907 (Δ -2.093)
- **pass_rate / overall**: 0.692 → 0.0 (Δ -0.692)
- **dimension / relevance**: 4.067 → 3.167 (Δ -0.9)
- **dimension / accuracy**: 4.481 → 4.0 (Δ -0.481)
- **dimension / completeness**: 3.894 → 2.333 (Δ -1.561)
- **dimension / usefulness**: 4.067 → 2.63 (Δ -1.437)
- **latency / p95_latency_ms**: 136871.36 → 192475.68 (Δ 55604.32)

## Optimization suggestions

- Weakest dimension 'completeness' (2.333): Add checklists to specialist skills (e.g. always request order id) so answers cover required parts.
- Category 'ambiguous' pass-rate 0.0: review those cases; add targeted skill guidance.
- 'llm' is 72.9% of measured time: LLM calls dominate: keep reasoning off for routing/critic, cache, or use a smaller worker model.
- P95 latency 192475.68ms is high (>20s): reduce max_react_steps, disable reasoning on the hot path, or shrink the planner/critic model.

## Failing / flagged cases

- `amb-01` (ambiguous) mean=2.907
- `amb-02` (ambiguous) mean=2.907
- `amb-03` (ambiguous) mean=3.282
