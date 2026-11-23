# paper2code-evals

Runs the plain-Claude baseline on PaperBench papers in a sealed Docker container, logs everything, and grades the result separately with PaperBench's own judge. See [EXPERIMENT_STRUCTURE.md](EXPERIMENT_STRUCTURE.md) for the overall plan.

## Layout

| Path | What it holds | Seen by the agent? |
|------|---------------|--------------------|
| `baseline_prompt.md` | The fixed baseline prompt | Yes (sent as the prompt) |
| `config/baseline.json` | Model, time limit, budget, tools, CPU/RAM limits | No |
| `docker/agent.Dockerfile` | The agent's environment (Ubuntu 24.04, Python 3.12 venv, Claude Code 2.1.322) | n/a |
| `tasks/<paper>/paper/` | The paper, addendum, blacklist, and figures only | Yes, read-only at `/home/paper` |
| `runs/paperbench/<paper>/<setup>/<run_id>/` | Everything from one run | Only `submission/` (at `/home/submission`) |
| `runs/index.jsonl` | One line per run, for comparing runs | No |
| `benchmarks/frontier-evals/` | PaperBench, including rubrics | No, never mounted |
| `grading/` | Grader image, grading script, scores | No, never mounted |

## One-time setup

1. Create an Anthropic API key in a **dedicated Console workspace** and set a monthly spend limit on that workspace. This is the only hard dollar cap. Claude Code's `--max-budget-usd` is its own estimate, checked between turns, so one long turn can go past it.
2. Copy `.env.example` to `.env` and fill in `ANTHROPIC_API_KEY`. Or, to use a Claude subscription instead of API credit, fill in `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`. Subscription runs count against the plan's usage limits and have no dollar cap, so `--max-budget-usd` only shapes how long the agent keeps going.
3. Build the images:

   ```
   docker build -f docker/agent.Dockerfile -t p2c-agent:1 docker
   docker build -f grading/grader.Dockerfile -t p2c-grader:1 benchmarks/frontier-evals/project
   ```

4. Fetch the paper and make the agent's task folder:

   ```
   python scripts/hydrate_lfs.py bam
   python scripts/prepare_task.py bam
   ```

## Running

```
python scripts/run_baseline.py bam --mode smoke-sandbox   # isolation + timeout checks, free, ~2 min
python scripts/run_baseline.py bam --mode smoke-claude    # tiny real Claude call, under $0.50
python scripts/run_baseline.py bam                        # the 90-minute baseline
```

To get several runs per paper, use the batch runner. It runs one at a time, takes turns between papers, and skips runs already in `runs/index.jsonl`, so you can stop it and start it again later. It stops after two failed or invalid runs in a row.

```
python scripts/run_batch.py bam sequential-neural-score-estimation all-in-one --runs-per-paper 3
```

Each run folder contains:

- `trace.jsonl`: Claude Code's full stream-json output, written line by line as it arrives.
- `tool_calls.jsonl`: every tool call paired with its result, including shell commands and their output.
- `claude_home/`: Claude Code's own session files from inside the container.
- `submission/`: the code, tests, `README.md`, `RESULTS.md`, and results the agent produced.
- `metadata.json`: start and end time, runtime, status, model, CLI version, image ID, token use, estimated cost, exit code, tool counts, blacklist hits, and a summary of the submission.
- `prompt.md`, `config.json`, `stderr.log`.

Run statuses are `completed`, `timed_out`, `budget_exceeded`, `error`, `runner_crashed`, and `invalid_host_slept`. If `metadata.json` still says `running`, the runner process itself was killed.

**Keep the laptop awake.** If Windows sleeps, the container freezes, and when it wakes the agent sees its clock jump forward and stops early. The runner asks Windows not to sleep while a run is going, but closing the lid or pressing the power button still forces sleep. Keep it plugged in with the lid open. If the runner sees a clock gap of more than 60 seconds, it marks the run `invalid_host_slept` and lists the gaps under `host_sleep_gaps`. The agent's own outcome is kept in `agent_status`.

## How the sandbox works

- The container runs as a non-root user with no sudo, all Linux capabilities dropped, and no Docker socket.
- Only three folders are mounted: the task folder (read-only), the run's `submission/`, and the run's `claude_home/`. No host drive, home folder, or rubric is visible.
- Only `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` is passed in. The agent can read it, so use the dedicated, capped workspace key from setup step 1, or a subscription token you can revoke.
- Claude Code runs with an explicit tool allow-list (`--allowedTools`), not with permission checks turned off.
- Internet stays on, because the agent needs the API, `pip`, and web search.
- The time limit is enforced twice. The runner kills the container at 90 minutes, and a `timeout` inside the container ends it 60 seconds later if the runner has died.

The blacklist (the authors' repo) is handled the same way PaperBench handles it, plus one block:

1. The prompt forbids it.
2. `git clone` of a blacklisted repo is redirected to a dead address.
3. After the run, every tool call's input is scanned for blacklisted URLs, and any hits are listed in `metadata.json` under `blacklist_hits`. A run with hits should be thrown out.

Fetching the repo through the web tools or `curl` is not blocked in advance. Step 3 catches it afterwards.

## Grading (separate step)

```
python grading/grade_run.py runs/paperbench/bam/baseline/<run_id>                # Code-Dev score
python grading/grade_run.py runs/paperbench/bam/baseline/<run_id> --judge dummy  # free plumbing test
```

This uses PaperBench's `run_judge.py` with judge `gpt-5-mini-2025-08-07` at high reasoning effort (PaperBench's default, `o3-mini-2025-01-31`, has been retired by OpenAI) and the unchanged rubric. It needs `OPENAI_API_KEY` in your environment. Scores go to `grading/results/`.

- **Code-Dev** (the default) checks only whether the code implements what the paper describes. It needs no GPU, so it fits this machine.
- **Full PaperBench** also runs `reproduce.sh` and checks the results. That needs an NVIDIA GPU host (PaperBench uses an A10) with the NVIDIA container toolkit, plus PaperBench's reproduction pipeline. This machine has no NVIDIA GPU, so full grading needs a cloud GPU box.

## Differences from PaperBench's own agent setup

- The prompt is PaperBench's `instructions.txt`, with these changes:
  - the time limit is set to 90 minutes and the environment is described (CPU-only, no root)
  - the line about an optional rubric is removed, since none is ever provided
  - the reproduction container's GPU and 7-day runtime limit are no longer mentioned
  - `RESULTS.md` is now required
- No GPU and no Docker-in-Docker inside the agent container.
- `rubric.json` is never given to the agent.
