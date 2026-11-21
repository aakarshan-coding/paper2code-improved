# Experiment Structure

## Goal

Find out whether a structured paper-to-code harness implements research papers more correctly than plain Claude given only the paper and the instruction "implement this paper."

## Hypotheses in scope

| ID | Hypothesis |
|----|------------|
| H1 | A separate step that pulls requirements out of the paper before any coding improves results over having one agent plan and code. |
| H2 | Tests built from the paper's formulas and algorithms catch more scientific errors than ordinary LLM-written unit tests. |
| H3 | Linking each requirement to the code and test that cover it improves correctness and makes fixes faster. |
| H4 | Marking each requirement as either stated in the paper or guessed by the agent reduces wrong guesses. |

H5 (choosing papers by how testable their claims are) is on hold and will be handled as a separate experiment later.

## Setups being compared

Each setup adds one piece to the previous one, so we can see what each piece contributes.

| Setup | What it adds | Tests |
|-------|--------------|-------|
| A. Baseline | Claude Code gets the paper and one prompt: "implement this paper and reproduce its main results." | Reference point |
| A+. Baseline with extra budget | Same as A, but with the same total budget as the full harness (for example, several attempts with the best one kept). | Rules out "it only won because it spent more" |
| B. + Requirement extraction | A separate step writes a requirements list, with each item pointing to the paper section or equation it came from. Coding works from that list. | H1 |
| C. + Stated vs. guessed | Each requirement is marked as stated or guessed. Guesses are written down as assumptions. | H4 |
| D. + Requirement-to-code links and repair loop | A table links each requirement to its code and test. The agent writes, runs, and fixes continuously until every requirement is covered. | H3 |
| E. + Formula-based tests | Tests are generated from the extracted equations and algorithms, separately from the code. | H2 |
| E-branch. + Try several readings | Where the paper is unclear, try 2–3 interpretations and keep the one with the best results. | The ambiguity question |

All setups use the same model version, tools, machine, and limits, except A+, which gets extra budget on purpose.

## Benchmarks

| Benchmark | What it checks | Main use |
|-----------|----------------|----------|
| PaperBench (start with the Code-Dev subset) | Overall replication, scored against a detailed rubric | Headline comparison |
| ResearchCodeBench | Whether core algorithm functions pass hidden tests | H2, H3 (is the math right?) |
| EXP-Bench | Whether the experiment setup and evaluation are complete | Checks that no core part of the experiment is missing |
| SA-Bench | TBD: confirm what it measures before assigning it | TBD |

Papers are split into two groups:
- **Dev set:** used while building and tuning the harness.
- **Test set:** used only for the final numbers. Prefer papers published after the model's training cutoff, so the model can't recall the original code.

## How each run works

1. Start a fresh Docker container from one fixed image with pinned Python, CUDA, and library versions. On Windows, run Docker through WSL2.
2. Run the setup non-interactively (`claude -p`), with the model version pinned and hard limits on turns, cost, and wall-clock time.
3. Block access to the paper authors' original code. Normal tools such as `pip install` stay allowed.
4. Save everything to `runs/<benchmark>/<paper>/<setup>/<seed>/`:
   - the full conversation log
   - the final code
   - `summary.json`: cost, time, stop reason, model version
   - the exact prompt and settings used
5. Skip a run if its folder is already complete, so a crashed batch can be restarted safely.

Grading is a separate step that reads saved run folders and uses each benchmark's official grader. Old runs can be re-graded at any time.

## Stability check before scaling up

1. Pick 2–3 dev papers.
2. Run the baseline 3 times on each.
3. Check how much scores vary between runs of the same paper. If they vary a lot, plan more repeat runs.
4. Read some logs to confirm failures come from the task itself, not from setup problems such as a missing GPU, a broken install, or hitting a limit too early.

## What gets measured

- **Score** from each benchmark's official grader.
- **Cost and time** per run.
- **Failure types**, labeled by hand on about 20–30 failed runs:
  - wrong math or algorithm
  - missing experiment part
  - made-up hyperparameter
  - requirement skipped or faked
  - setup or environment failure
- **Repair effort** (setups D and E): number of fix cycles needed to get from first draft to passing.

## Comparing results

- Run each setup on each paper at least 3 times.
- Compare setups paper by paper (setup B on paper X vs. baseline on paper X), because papers differ far more from each other than setups do.
- Report the average difference together with a range showing how sure we are (bootstrap confidence intervals).

## Writeup outputs

1. Main table: setup × benchmark, with score, uncertainty range, and cost.
2. Score vs. cost chart for every setup.
3. Failure-type breakdown for each setup.
4. 2–3 case studies where the harness caught an error the baseline missed.
