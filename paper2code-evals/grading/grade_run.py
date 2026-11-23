"""Grade one finished run with PaperBench's own judge (paperbench/scripts/run_judge.py), inside the p2c-grader container.

This never runs during generation and only reads the run folder. The submission is copied,
reduced to its committed files (PaperBench runs `git clean -fd` before grading), and graded
from the copy. The grader container, not the agent container, is the only place the rubric
and OPENAI_API_KEY exist.

    python grading/grade_run.py runs/paperbench/bam/baseline/<run_id>                 # Code-Dev, gpt-5-mini judge
    python grading/grade_run.py runs/paperbench/bam/baseline/<run_id> --full          # every rubric node
    python grading/grade_run.py runs/paperbench/bam/baseline/<run_id> --judge dummy   # free plumbing test
"""
import argparse
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "benchmarks" / "frontier-evals" / "project" / "paperbench" / "data"
IMAGE = "p2c-grader:1"
JUDGE_MODEL = "gpt-5-mini-2025-08-07"  # PaperBench default o3-mini-2025-01-31 was retired by OpenAI; fixed replacement for all comparisons


def force_remove(func, path, _exc):
    """git marks its object files read-only, which blocks deletion on Windows."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def iter_leaves(node: dict):
    if not node.get("sub_tasks"):
        yield node
    for child in node.get("sub_tasks", []):
        yield from iter_leaves(child)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--full", action="store_true", help="grade every rubric node, not only Code Development")
    parser.add_argument("--judge", choices=["simple", "dummy"], default="simple")
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    meta = json.loads((run_dir / "metadata.json").read_text())
    variant = ("full" if args.full else "code_dev") + ("" if args.judge == "simple" else f"-{args.judge}")
    out_dir = ROOT / "grading" / "results" / meta["paper"] / meta["setup"] / meta["run_id"] / variant
    work = ROOT / "grading" / "work" / meta["run_id"] / "submission"

    if work.exists():
        shutil.rmtree(work, onexc=force_remove)
    shutil.copytree(run_dir / "submission", work)
    if (work / ".git").exists():
        subprocess.run(["git", "-c", "safe.directory=*", "-C", str(work), "clean", "-fd"], check=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = ["docker", "run", "--rm",
           "--mount", f"type=bind,source={DATA},target=/data,readonly",
           "--mount", f"type=bind,source={work},target=/submission",
           "--mount", f"type=bind,source={out_dir},target=/out"]
    judge_args = []
    if args.judge == "simple":
        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("OPENAI_API_KEY is not set (the PaperBench judge uses OpenAI)")
        cmd += ["-e", "OPENAI_API_KEY"]
        judge_args = [
            "completer_config=preparedness_turn_completer.oai_completions_turn_completer:OpenAICompletionsTurnCompleter.Config",
            f"completer_config.model={JUDGE_MODEL}", "completer_config.reasoning_effort=high",
        ]
    cmd += [IMAGE, "uv", "run", "--frozen", "python", "paperbench/scripts/run_judge.py",
            "submission_path=/submission", f"paper_id={meta['paper']}", f"judge={args.judge}",
            f"code_only={not args.full}", "out_dir=/out", *judge_args]
    subprocess.run(cmd, check=True)

    output = json.loads((out_dir / "grader_output.json").read_text())
    if output["num_invalid_leaf_nodes"]:
        sample = next(n for n in iter_leaves(output["graded_task_tree"]) if not n.get("valid_score"))
        raise SystemExit(f"{output['num_invalid_leaf_nodes']}/{output['num_leaf_nodes']} rubric items failed to grade; "
                         f"score not recorded. First error: {sample.get('explanation', '')[:300]}")
    row = {"run_id": meta["run_id"], "paper": meta["paper"], "setup": meta["setup"], "variant": variant,
           "judge": args.judge, "judge_model": JUDGE_MODEL if args.judge == "simple" else None,
           "score": output["score"], "graded_at": output["graded_at"],
           "leaf_nodes": output["num_leaf_nodes"], "invalid_leaf_nodes": output["num_invalid_leaf_nodes"],
           "grader_output": str((out_dir / "grader_output.json").relative_to(ROOT)).replace("\\", "/")}
    with (ROOT / "grading" / "results" / "index.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps(row, indent=2))


if __name__ == "__main__":
    main()
