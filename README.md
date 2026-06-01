# Agent CI Failure Packet

Turn noisy CI logs into a compact failure packet for coding agents and human reviewers.

When CI fails after an agent change, the next run needs the right context: failing command, likely error lines, touched files, and a retry plan. `agent-ci-failure-packet` reads plain text logs and produces Markdown or JSON that can be pasted back into Codex, Claude Code, or a PR comment.

## What It Extracts

- Failing commands from common shell and CI patterns.
- Error, failure, traceback, and exception lines.
- File references with line numbers.
- Test summary lines.
- Suggested next checks.
- A compact prompt block for the next agent run.

The tool is local-first and dependency-free. It does not call CI APIs; it works on logs you already have.

## Install

```sh
python -m pip install --upgrade pip
python -m pip install -e .
```

Or run without installing:

```sh
PYTHONPATH=src python -m agent_ci_failure_packet examples/ci-failure.log
```

## Usage

Create a Markdown packet:

```sh
agent-ci-failure-packet examples/ci-failure.log --title "Publish guard CI failure"
```

Create JSON for automation:

```sh
agent-ci-failure-packet examples/ci-failure.log --format json
```

Read from stdin:

```sh
pbpaste | agent-ci-failure-packet - --title "Latest CI failure"
```

## Example Output

```md
# CI Failure Packet: Publish guard CI failure

## Failing Commands

- `python -m unittest discover -s tests`

## Error Signals

- `FAIL: test_blocks_unexpected_paths`
- `AssertionError: expected exit code 1, got 0`

## Referenced Files

- `tests/test_publish_guard.py:42`
- `scripts/commit_daily_update.sh:71`

## Next Agent Prompt

Fix the CI failure using the evidence above. Keep the change scoped, rerun the failing command, and explain the verification result.
```

## Development

```sh
PYTHONPATH=src python -m unittest discover -s tests
PYTHONPATH=src python -m agent_ci_failure_packet examples/ci-failure.log --format json
```

## Fit With The Agent Workflow Stack

- `agent-task-contract`: make the task specific before the run.
- `repo-flightcheck`: confirm the repo is ready.
- `agent-secret-sentinel`: catch secret leaks in diffs.
- `agent-ci-failure-packet`: turn CI failures into focused retry context.
- `diff-to-eval`: save useful failures as future eval cases.
- `agent-run-ledger`: keep the run auditable.

