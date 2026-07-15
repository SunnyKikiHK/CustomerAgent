"""CLI to run the agent evaluation set and write results + a report.

Usage (from the repo root, inside the WSL venv):

    # Live: run every case through the real pipeline (needs infra + OPENROUTER_API_KEY)
    python scripts/run_evaluation.py

    # Live on a subset (faster smoke of the harness)
    python scripts/run_evaluation.py --limit 12

    # Offline: heuristic judge only, no pipeline (for a quick harness check)
    python scripts/run_evaluation.py --offline

    # Compare against a saved baseline and/or promote this run to baseline
    python scripts/run_evaluation.py --baseline eval_results/baseline.json --promote

Outputs land in ``eval_results/`` (eval_results.json + eval_report.md).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from packages.evaluation.src.cases import ALL_CASES  # noqa: E402
from packages.evaluation.src.runner import run_evaluation, write_results  # noqa: E402


def _load_baseline(path: str | None) -> dict | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        print(f"baseline not found at {p}; running without regression comparison")
        return None
    return json.loads(p.read_text(encoding="utf-8"))


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Run the agent evaluation set.")
    parser.add_argument("--offline", action="store_true", help="Heuristic judge, no pipeline.")
    parser.add_argument("--limit", type=int, default=0, help="Only run the first N cases.")
    parser.add_argument("--baseline", type=str, default=None, help="Baseline JSON to compare against.")
    parser.add_argument("--promote", action="store_true", help="Save this run as the new baseline.")
    parser.add_argument("--out", type=str, default="eval_results", help="Output directory.")
    args = parser.parse_args()

    cases = ALL_CASES[: args.limit] if args.limit > 0 else ALL_CASES
    mode = "offline" if args.offline else "live"
    baseline = _load_baseline(args.baseline)

    print(f"Running {len(cases)} cases in {mode} mode...")
    run = await run_evaluation(cases=cases, mode=mode, baseline=baseline)
    paths = write_results(run, out_dir=args.out)

    if args.promote:
        baseline_path = Path(args.out) / "baseline.json"
        shutil.copyfile(paths["json"], baseline_path)
        print(f"promoted this run to baseline: {baseline_path}")

    s = run.summary()
    print(f"\n=== {mode} run {run.run_id} ===")
    print(f"cases={s['cases']} passed={s['passed']} pass_rate={s['pass_rate'] * 100:.1f}%")
    print(f"dimension_means={s['dimension_means']}")
    print(f"metrics={json.dumps(run.metrics, indent=2)}")
    if run.regressions:
        print(f"regressions={json.dumps(run.regressions, indent=2)}")
    print(f"\nwrote: {paths['json']}\n       {paths['report']}")


if __name__ == "__main__":
    asyncio.run(_main())
