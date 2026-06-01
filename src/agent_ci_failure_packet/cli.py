from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


ERROR_PATTERNS = [
    r"\bERROR\b",
    r"\bFAIL\b",
    r"\bFAILED\b",
    r"AssertionError",
    r"Traceback \(most recent call last\)",
    r"Exception:",
    r"Error:",
    r"exit code \d+",
    r"Process completed with exit code \d+",
]

COMMAND_PATTERNS = [
    r"^\+\s+(.+)$",
    r"^Run\s+(.+)$",
    r"^\$ ([^\n]+)$",
    r"^>\s+([^<].+)$",
]

FILE_PATTERNS = [
    re.compile(r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9]+):(?P<line>\d+)"),
    re.compile(r"File \"(?P<path>[^\"]+)\", line (?P<line>\d+)"),
]


@dataclass(frozen=True)
class FileRef:
    path: str
    line: int


@dataclass(frozen=True)
class FailurePacket:
    schema_version: str
    title: str
    failing_commands: list[str]
    error_signals: list[str]
    referenced_files: list[FileRef]
    test_summaries: list[str]
    suggested_checks: list[str]
    next_agent_prompt: str


def read_log(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8", errors="replace")


def compact(line: str, limit: int = 220) -> str:
    line = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
    if len(line) <= limit:
        return line
    return line[: limit - 3] + "..."


def unique(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
        if len(result) >= limit:
            break
    return result


def extract_commands(lines: list[str]) -> list[str]:
    commands: list[str] = []
    for line in lines:
        stripped = compact(line)
        for pattern in COMMAND_PATTERNS:
            match = re.match(pattern, stripped)
            if match:
                command = match.group(1).strip()
                if command and not command.startswith(("echo ", "printf ")):
                    commands.append(command)
    return unique(commands, 8)


def extract_errors(lines: list[str]) -> list[str]:
    errors: list[str] = []
    for line in lines:
        stripped = compact(line)
        if any(re.search(pattern, stripped) for pattern in ERROR_PATTERNS):
            errors.append(stripped)
    return unique(errors, 12)


def extract_file_refs(lines: list[str]) -> list[FileRef]:
    refs: list[FileRef] = []
    seen: set[tuple[str, int]] = set()
    for line in lines:
        for pattern in FILE_PATTERNS:
            for match in pattern.finditer(line):
                ref = (match.group("path"), int(match.group("line")))
                if ref not in seen:
                    seen.add(ref)
                    refs.append(FileRef(ref[0], ref[1]))
                if len(refs) >= 12:
                    return refs
    return refs


def extract_test_summaries(lines: list[str]) -> list[str]:
    summaries: list[str] = []
    for line in lines:
        stripped = compact(line)
        if re.search(r"\b\d+\s+(failed|passed|skipped|errors?)\b", stripped, re.IGNORECASE):
            summaries.append(stripped)
        elif re.search(r"Ran \d+ tests?", stripped):
            summaries.append(stripped)
        elif re.search(r"^FAILED \(", stripped):
            summaries.append(stripped)
    return unique(summaries, 8)


def suggest_checks(commands: list[str], refs: list[FileRef]) -> list[str]:
    checks: list[str] = []
    for command in commands:
        if any(keyword in command for keyword in ["test", "unittest", "pytest", "npm", "pnpm", "yarn", "swift test"]):
            checks.append(command)
    if refs:
        paths = sorted({ref.path for ref in refs})[:4]
        checks.append("Inspect referenced files: " + ", ".join(paths))
    if not checks and commands:
        checks.append(commands[-1])
    if not checks:
        checks.append("Rerun the failing CI job with verbose logs.")
    return unique(checks, 6)


def build_prompt(title: str, errors: list[str], checks: list[str]) -> str:
    first_error = errors[0] if errors else "the CI failure shown above"
    first_check = checks[0] if checks else "the failing CI command"
    return (
        f"Fix the CI failure for '{title}'. Start from this evidence: {first_error}. "
        f"Keep the patch scoped, rerun `{first_check}`, and report the exact verification result."
    )


def build_packet(log_text: str, title: str) -> FailurePacket:
    lines = log_text.splitlines()
    commands = extract_commands(lines)
    errors = extract_errors(lines)
    refs = extract_file_refs(lines)
    summaries = extract_test_summaries(lines)
    checks = suggest_checks(commands, refs)
    return FailurePacket(
        schema_version="agent-ci-failure-packet.v1",
        title=title,
        failing_commands=commands,
        error_signals=errors,
        referenced_files=refs,
        test_summaries=summaries,
        suggested_checks=checks,
        next_agent_prompt=build_prompt(title, errors, checks),
    )


def render_markdown(packet: FailurePacket) -> str:
    lines = [f"# CI Failure Packet: {packet.title}", ""]

    sections: list[tuple[str, list[str]]] = [
        ("Failing Commands", [f"`{command}`" for command in packet.failing_commands]),
        ("Error Signals", [f"`{error}`" for error in packet.error_signals]),
        ("Referenced Files", [f"`{ref.path}:{ref.line}`" for ref in packet.referenced_files]),
        ("Test Summaries", [f"`{summary}`" for summary in packet.test_summaries]),
        ("Suggested Checks", [f"`{check}`" for check in packet.suggested_checks]),
    ]

    for heading, items in sections:
        lines.extend([f"## {heading}", ""])
        if items:
            lines.extend(f"- {item}" for item in items)
        else:
            lines.append("- No clear signal found.")
        lines.append("")

    lines.extend(["## Next Agent Prompt", "", packet.next_agent_prompt, ""])
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-ci-failure-packet")
    parser.add_argument("log", help="Path to CI log, or '-' to read from stdin.")
    parser.add_argument("--title", default="CI failure", help="Packet title.")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--output", help="Write output to this path instead of stdout.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    log_text = read_log(args.log)
    if not log_text.strip():
        print("No CI log content provided.", file=sys.stderr)
        return 2

    packet = build_packet(log_text, args.title)
    output = json.dumps(asdict(packet), indent=2) if args.format == "json" else render_markdown(packet)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output if output.endswith("\n") else output + "\n", encoding="utf-8")
        print(output_path)
    else:
        print(output, end="" if output.endswith("\n") else "\n")
    return 0
