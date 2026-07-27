# Agent Evaluation Report

- Run: `f84f2493ebc1`  mode: **live**
- Cases: **5**  Passed: **5**  Pass rate: **100.0%**

## LLM-as-Judge (mean 1-5)

| Dimension | Mean |
|---|---|
| relevance | 5.0 |
| accuracy | 5.0 |
| completeness | 5.0 |
| usefulness | 5.0 |

## By category

| Category | N | Pass rate | Mean |
|---|---|---|---|
| ambiguous | 5 | 100.0% | 5.0 |

## Performance metrics

- P50 latency: **507908.69 ms**
- P95 latency: **576883.35 ms**
- Avg latency: **523845.56 ms**
- Tokens per successful task: **4516.6**
- Cost per resolution: **$0.0**

### Time breakdown (reasoning vs tool vs LLM)

- reasoning: **49.9%**
- tool execution: **45.2%**
- LLM calls: **0.0%**
- other: **4.9%**

## Regressions vs baseline

- None detected.

## Optimization suggestions

- P95 latency 576883.35ms is high (>20s): reduce max_react_steps, disable reasoning on the hot path, or shrink the planner/critic model.

## Failing / flagged cases

- None.
