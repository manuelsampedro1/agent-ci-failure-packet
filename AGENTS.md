# AGENTS.md

## Goal

Keep `agent-ci-failure-packet` a small, dependency-free CLI that turns noisy CI logs into compact retry packets for coding agents and human reviewers.

## Constraints

- Prefer Python standard library only.
- Do not call CI APIs; the tool works on logs supplied by the user.
- Keep Markdown and JSON output stable unless tests and README examples are updated together.
- Do not include secrets or private CI logs in fixtures.
- Preserve enough evidence for a next agent run: failing commands, error signals, referenced files, summaries, checks, and a focused prompt.

## Verification

Run before closing changes:

```sh
make test
make lint
make build
make smoke
git diff --check
```

## Commit Expectations

- Commit parser changes with tests.
- Keep fixtures short and reviewable.
- Do not publish dirty trees, generated packet files, or local cache output.
