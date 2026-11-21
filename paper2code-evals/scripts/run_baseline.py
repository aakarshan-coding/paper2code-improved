"""Launch one baseline run of Claude Code on one PaperBench paper, inside a fresh Docker container.

Usage:
    python scripts/run_baseline.py bam                    # the real 90-minute baseline
    python scripts/run_baseline.py bam --mode smoke-sandbox   # isolation + timeout checks, no API calls
    python scripts/run_baseline.py bam --mode smoke-claude    # tiny paid Claude call (< $0.50)

Every run gets its own folder under runs/. The folder is filled while the run
is going, so a timeout or crash still leaves the trace, the code, and metadata.json.
"""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUTH_VARS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
NETWORK_TOOLS = {"Bash", "WebFetch", "WebSearch"}
WATCHDOG_POLL_S = 5
SLEEP_GAP_S = 60
SMOKE_CLAUDE_PROMPT = (
    "Create /home/submission/hello.py that prints 'ok', run it, and commit it in a new git repo "
    "at /home/submission. Then check whether /home/paper/rubric.json exists and say so in one line."
)

MODES = {
    "baseline": {"runs_subdir": None, "timeout_s": None, "budget_usd": None},
    "smoke-sandbox": {"runs_subdir": "smoke", "timeout_s": 120, "budget_usd": 0},
    "smoke-claude": {"runs_subdir": "smoke", "timeout_s": 300, "budget_usd": 0.5},
}


def keep_host_awake(on: bool) -> None:
    """Ask Windows not to idle-sleep while a run is going. Closing the lid can still force standby."""
    if sys.platform == "win32":
        import ctypes
        es_continuous, es_system_required = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | (es_system_required if on else 0))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def load_dotenv(path: Path) -> dict:
    if not path.exists():
        return {}
    pairs = (line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.lstrip().startswith("#"))
    return {k.strip(): v.strip().strip('"').strip("'") for k, v in pairs}


def capture(cmd: list) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unavailable: {exc!r}"


def blacklist_git_rewrites(urls: list) -> dict:
    """Env-based git config that rewrites every blacklisted repo URL to an unreachable host."""
    prefixes = []
    for url in urls:
        prefixes += [url, url.lower()]
        if url.startswith("https://github.com/"):
            prefixes.append("git@github.com:" + url.removeprefix("https://github.com/"))
    env = {"GIT_CONFIG_COUNT": str(len(prefixes))}
    for i, prefix in enumerate(prefixes):
        env[f"GIT_CONFIG_KEY_{i}"] = "url.https://blocked.invalid/.insteadOf"
        env[f"GIT_CONFIG_VALUE_{i}"] = prefix
    return env


def blacklist_needles(urls: list) -> list:
    needles = set()
    for url in urls:
        bare = url.lower().split("://", 1)[-1].rstrip("/")
        needles.add(bare)
        parts = bare.split("/")
        if len(parts) >= 3:
            needles.add("/".join(parts[1:3]))
    return sorted(needles)


def docker_command(name: str, cfg: dict, mode: str, task_dir: Path, run_dir: Path, env_names: list, budget: float, blacklist: list, timeout_s: int) -> list:
    mounts = [
        (task_dir, "/home/paper", True),
        (run_dir / "submission", "/home/submission", False),
        (run_dir / "claude_home", "/home/agent/.claude", False),
    ]
    res = cfg["resources"]
    cmd = [
        "docker", "run", "--rm", "-i", "--init", "--name", name,
        "--cpus", res["cpus"], "--memory", res["memory"], "--pids-limit", str(res["pids_limit"]),
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
    ]
    for src, dst, ro in mounts:
        cmd += ["--mount", f"type=bind,source={src},target={dst}" + (",readonly" if ro else "")]
    git_env = blacklist_git_rewrites(blacklist)
    extra = {"EXPECTED_CLAUDE_VERSION": cfg["claude_code_version"], "BLACKLIST_PROBE_URL": blacklist[0] if blacklist else ""}
    for key, value in {**git_env, **extra}.items():
        cmd += ["-e", f"{key}={value}"]
    for key in env_names:
        cmd += ["-e", key]
    cmd.append(cfg["image"])
    # Backstop in case this runner process dies: the container ends itself shortly after the external deadline.
    cmd += ["timeout", "--kill-after=30", str(timeout_s + 60)]
    if mode == "smoke-sandbox":
        return cmd + ["bash", "/opt/checks/sandbox_check.sh"]
    return cmd + [
        "claude", "-p",
        "--output-format", "stream-json", "--verbose",
        "--model", cfg["model"],
        "--max-budget-usd", str(budget),
        "--permission-mode", "acceptEdits",
        "--allowedTools", ",".join(cfg["allowed_tools"]),
        "--add-dir", "/home/paper",
        "--strict-mcp-config",
    ]


def summarize_trace(trace_path: Path, needles: list) -> dict:
    events, bad_lines = [], 0
    for line in trace_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            bad_lines += 1

    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)

    usage_by_msg, tool_uses, tool_results = {}, {}, {}
    for e in events:
        msg = e.get("message") or {}
        if e.get("type") == "assistant":
            if msg.get("id") and msg.get("usage"):
                usage_by_msg[msg["id"]] = msg["usage"]
            for block in msg.get("content") or []:
                if block.get("type") == "tool_use":
                    tool_uses[block["id"]] = block
        elif e.get("type") == "user" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if block.get("type") == "tool_result":
                    tool_results[block.get("tool_use_id")] = block

    token_keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    summed = {k: sum(u.get(k) or 0 for u in usage_by_msg.values()) for k in token_keys}

    tool_calls = []
    hits = []
    for i, (tid, use) in enumerate(tool_uses.items()):
        res = tool_results.get(tid, {})
        tool_calls.append({"i": i, "tool": use.get("name"), "input": use.get("input"),
                           "result": res.get("content"), "is_error": res.get("is_error", False)})
        if use.get("name") not in NETWORK_TOOLS:
            continue
        text = json.dumps(use.get("input"), ensure_ascii=False).lower()
        hits += [{"tool_call": i, "tool": use.get("name"), "needle": n} for n in needles if n in text]

    with (trace_path.parent / "tool_calls.jsonl").open("w", encoding="utf-8") as f:
        for call in tool_calls:
            f.write(json.dumps(call, ensure_ascii=False) + "\n")

    keep = ("subtype", "is_error", "num_turns", "duration_ms", "duration_api_ms", "total_cost_usd", "usage", "modelUsage", "stop_reason")
    return {
        "events": len(events),
        "unparseable_lines": bad_lines,
        "session_id": init.get("session_id"),
        "model_reported": init.get("model"),
        "claude_code_version_reported": init.get("claude_code_version"),
        "tools_available": init.get("tools"),
        "result_event": {k: result.get(k) for k in keep if k in result} if result else None,
        "tokens_summed_from_messages": summed,
        "tool_call_counts": dict(Counter(c["tool"] for c in tool_calls)),
        "tool_errors": sum(1 for c in tool_calls if c["is_error"]),
        "blacklist_hits": hits,
        "sandbox_checks": [e for e in events if e.get("type") == "sandbox_check"],
    }


def summarize_submission(sub: Path) -> dict:
    files = [p for p in sub.rglob("*") if p.is_file() and ".git" not in p.relative_to(sub).parts]
    git = ["git", "-c", "safe.directory=*", "-C", str(sub)]
    is_repo = (sub / ".git").exists()
    return {
        "file_count": len(files),
        "total_bytes": sum(p.stat().st_size for p in files),
        "has_reproduce_sh": (sub / "reproduce.sh").exists(),
        "has_readme": (sub / "README.md").exists(),
        "has_results_md": (sub / "RESULTS.md").exists(),
        "is_git_repo": is_repo,
        "git_commits": capture(git + ["rev-list", "--count", "HEAD"]) if is_repo else None,
        "git_untracked_or_modified": capture(git + ["status", "--porcelain"]).splitlines() if is_repo else None,
    }


def classify(timed_out: bool, exit_code, trace: dict, mode: str) -> str:
    if mode == "smoke-sandbox":
        checks = trace["sandbox_checks"]
        return "passed" if timed_out and checks and all(c["ok"] for c in checks) else "failed"
    if timed_out:
        return "timed_out"
    result = trace.get("result_event") or {}
    if "budget" in str(result.get("subtype", "")):
        return "budget_exceeded"
    if exit_code == 0 and result and not result.get("is_error"):
        return "completed"
    return "error"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paper")
    parser.add_argument("--config", default="config/baseline.json")
    parser.add_argument("--mode", choices=sorted(MODES), default="baseline")
    args = parser.parse_args()

    cfg = json.loads((ROOT / args.config).read_text())
    mode = MODES[args.mode]
    task_dir = ROOT / "tasks" / args.paper / "paper"
    manifest_path = task_dir.parent / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"run `python scripts/prepare_task.py {args.paper}` first")
    manifest = json.loads(manifest_path.read_text())

    image_info = json.loads(capture(["docker", "image", "inspect", cfg["image"], "--format", "{{json .}}"]) or "{}")
    if not image_info:
        raise SystemExit(f"docker image {cfg['image']} not found; build it (see README.md)")
    cfg["claude_code_version"] = image_info["Config"]["Labels"]["claude_code_version"]

    env = {**os.environ, **load_dotenv(ROOT / ".env")}
    env_names = [k for k in AUTH_VARS if env.get(k)]
    if args.mode != "smoke-sandbox" and not env_names:
        raise SystemExit("no Claude credential: set ANTHROPIC_API_KEY (or CLAUDE_CODE_OAUTH_TOKEN) in .env")

    timeout_s = mode["timeout_s"] or cfg["timeout_minutes"] * 60
    budget = cfg["max_budget_usd"] if mode["budget_usd"] is None else mode["budget_usd"]
    prompt = (ROOT / cfg["prompt_file"]).read_text(encoding="utf-8") if args.mode == "baseline" else SMOKE_CLAUDE_PROMPT

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    group = mode["runs_subdir"] or cfg["benchmark"]
    run_dir = ROOT / "runs" / group / args.paper / (cfg["setup"] if args.mode == "baseline" else args.mode) / run_id
    (run_dir / "submission").mkdir(parents=True)
    (run_dir / "claude_home").mkdir()
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    (run_dir / "config.json").write_text(json.dumps({**cfg, "mode": args.mode, "timeout_s": timeout_s, "max_budget_usd": budget}, indent=2))

    name = f"p2c-{run_id.lower()}"
    cmd = docker_command(name, cfg, args.mode, task_dir, run_dir, env_names, budget, manifest["blacklist"], timeout_s)
    meta = {
        "run_id": run_id, "mode": args.mode, "setup": cfg["setup"], "benchmark": cfg["benchmark"], "paper": args.paper,
        "status": "running", "start_utc": utc_now(), "end_utc": None, "runtime_s": None,
        "timeout_s": timeout_s, "max_budget_usd": budget, "model_requested": cfg["model"],
        "claude_code_version_image": cfg["claude_code_version"], "image": cfg["image"], "image_id": image_info.get("Id"),
        "prompt_sha256": sha256_text(prompt), "task_manifest_sha256": sha256_text(manifest_path.read_text()),
        "docker_command": cmd, "auth_vars_passed": env_names,
        "host": {"platform": platform.platform(), "python": platform.python_version(),
                 "docker": capture(["docker", "version", "--format", "{{.Server.Version}}"])},
        "cost_note": "total_cost_usd is Claude Code's client-side estimate; --max-budget-usd is a soft cap checked between turns.",
    }
    meta_path = run_dir / "metadata.json"
    meta_path.write_text(json.dumps(meta, indent=2))
    print(f"run dir: {run_dir}\ntimeout: {timeout_s}s  budget: ${budget}  model: {cfg['model']}", flush=True)

    timed_out = threading.Event()
    sleep_gaps = []
    exit_code, error = None, None
    t0 = time.monotonic()
    proc = None
    keep_host_awake(True)
    try:
        with (run_dir / "trace.jsonl").open("w", encoding="utf-8") as trace_f, (run_dir / "stderr.log").open("w", encoding="utf-8") as err_f:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err_f, env=env, text=True, encoding="utf-8")

            def watchdog():
                # threading.Timer stalls while Windows is in standby, so poll the clocks instead.
                last = time.time()
                while proc.poll() is None:
                    time.sleep(WATCHDOG_POLL_S)
                    now = time.time()
                    if now - last > SLEEP_GAP_S:
                        sleep_gaps.append({"from_utc": datetime.fromtimestamp(last, timezone.utc).isoformat(timespec="seconds"),
                                           "seconds": round(now - last)})
                    last = now
                    if time.monotonic() - t0 > timeout_s and proc.poll() is None:
                        timed_out.set()
                        subprocess.run(["docker", "kill", name], capture_output=True)
                        return

            threading.Thread(target=watchdog, daemon=True).start()
            proc.stdin.write(prompt)
            proc.stdin.close()
            for line in proc.stdout:
                trace_f.write(line)
                trace_f.flush()
            exit_code = proc.wait()
    except BaseException as exc:
        error = repr(exc)
        subprocess.run(["docker", "kill", name], capture_output=True)
        if proc:
            proc.wait()
            exit_code = proc.returncode
    finally:
        keep_host_awake(False)
        trace = summarize_trace(run_dir / "trace.jsonl", blacklist_needles(manifest["blacklist"]))
        agent_status = "runner_crashed" if error else classify(timed_out.is_set(), exit_code, trace, args.mode)
        meta.update({
            "end_utc": utc_now(), "runtime_s": round(time.monotonic() - t0, 1),
            "container_exit_code": exit_code, "timed_out": timed_out.is_set(), "runner_error": error,
            "host_sleep_gaps": sleep_gaps, "agent_status": agent_status,
            "status": "invalid_host_slept" if sleep_gaps else agent_status,
            "trace": trace, "submission": summarize_submission(run_dir / "submission"),
        })
        meta_path.write_text(json.dumps(meta, indent=2))
        result = trace.get("result_event") or {}
        row = {k: meta[k] for k in ("run_id", "mode", "setup", "paper", "status", "start_utc", "runtime_s", "model_requested")}
        row.update({"cost_usd": result.get("total_cost_usd"), "num_turns": result.get("num_turns"),
                    "blacklist_hits": len(trace["blacklist_hits"]), "run_dir": str(run_dir.relative_to(ROOT)).replace("\\", "/")})
        with (ROOT / "runs" / "index.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(json.dumps(row, indent=2))
    return 0 if meta["status"] in ("completed", "passed", "timed_out", "budget_exceeded") else 1


if __name__ == "__main__":
    sys.exit(main())
