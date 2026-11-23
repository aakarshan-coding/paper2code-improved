"""Run baseline runs one at a time until each paper has the target number of valid runs.

    python scripts/run_batch.py bam sequential-neural-score-estimation all-in-one --runs-per-paper 3

Safe to stop and restart: valid runs already in runs/index.jsonl count toward the target.
Papers are interleaved (round-robin) so no paper gets all the runs from one time of day.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VALID = {"completed", "timed_out", "budget_exceeded"}
MAX_CONSECUTIVE_FAILURES = 2


def valid_runs(paper: str, setup: str) -> int:
    index = ROOT / "runs" / "index.jsonl"
    rows = [json.loads(line) for line in index.read_text().splitlines()] if index.exists() else []
    return sum(1 for r in rows if r["paper"] == paper and r["setup"] == setup and r["mode"] == "baseline" and r["status"] in VALID)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("papers", nargs="+")
    parser.add_argument("--runs-per-paper", type=int, default=3)
    parser.add_argument("--config", default="config/baseline.json")
    args = parser.parse_args()
    setup = json.loads((ROOT / args.config).read_text())["setup"]

    failures = 0
    while True:
        counts = {p: valid_runs(p, setup) for p in args.papers}
        print("valid runs so far:", counts, flush=True)
        todo = [p for p in args.papers if counts[p] < args.runs_per_paper]
        if not todo:
            print("all targets met")
            return 0
        paper = min(todo, key=lambda p: counts[p])
        code = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_baseline.py"), paper, "--config", args.config]).returncode
        failures = 0 if code == 0 and valid_runs(paper, setup) > counts[paper] else failures + 1
        if failures >= MAX_CONSECUTIVE_FAILURES:
            print(f"stopping: {failures} runs in a row failed or were invalid; check the latest metadata.json")
            return 1


if __name__ == "__main__":
    sys.exit(main())
