# Agent Evaluation Report

- Run: `09ca0cf4ec01`  mode: **live**
- Cases: **12**  Passed: **6**  Pass rate: **50.0%**

## LLM-as-Judge (mean 1-5)

| Dimension | Mean |
|---|---|
| relevance | 3.417 |
| accuracy | 3.417 |
| completeness | 3.167 |
| usefulness | 3.167 |

## By category

| Category | N | Pass rate | Mean |
|---|---|---|---|
| ambiguous | 8 | 50.0% | 3.312 |
| edge | 4 | 50.0% | 3.25 |

## Performance metrics

- P50 latency: **37140.98 ms**
- P95 latency: **145451.72 ms**
- Avg latency: **48429.1 ms**
- Tokens per successful task: **4407.8**
- Cost per resolution: **$0.0**

### Time breakdown (reasoning vs tool vs LLM)

- reasoning: **78.6%**
- tool execution: **2.7%**
- LLM calls: **18.7%**
- other: **0.0%**

## Regressions vs baseline

- None detected.

## Optimization suggestions

- Weakest dimension 'completeness' (3.167): Add checklists to specialist skills (e.g. always request order id) so answers cover required parts.
- Category 'ambiguous' pass-rate 0.5: review those cases; add targeted skill guidance.
- Category 'edge' pass-rate 0.5: review those cases; add targeted skill guidance.
- 'reasoning' is 78.6% of measured time: Planning dominates: consider the deterministic planner fast-path for simple turns.
- P95 latency 145451.72ms is high (>20s): reduce max_react_steps, disable reasoning on the hot path, or shrink the planner/critic model.

## Failing / flagged cases

- `amb-03` (ambiguous) mean=1.75
- `amb-04` (ambiguous) mean=2.25
- `amb-05` (ambiguous) mean=1.5
- `amb-07` (ambiguous) mean=2.25
- `edge-02` (edge) mean=1.5
- `edge-03` (edge) mean=2.0
