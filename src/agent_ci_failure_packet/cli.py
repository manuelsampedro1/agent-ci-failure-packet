from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


COMMAND_RECEIPT_SCHEMA = "agent-command-receipt.v1"

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
class CommandReceiptEvidence:
    path: str
    command: str
    status: str
    exit_code: int | None
    evidence_files: list[str]


@dataclass(frozen=True)
class FailurePacket:
    schema_version: str
    title: str
    command_receipt: CommandReceiptEvidence | None
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("command receipt JSON must be an object")
    return data


def resolve_evidence_path(base_dir: Path, path_text: str) -> Path:
    candidate = Path(path_text)
    if candidate.is_absolute():
        return candidate
    return base_dir / candidate


def read_failed_receipt(
    receipt_path: Path,
    base_dir: Path,
) -> tuple[str, CommandReceiptEvidence]:
    receipt = parse_json_object(receipt_path)
    errors: list[str] = []
    log_parts: list[str] = []
    checked_paths: list[str] = []

    if receipt.get("schema_version") != COMMAND_RECEIPT_SCHEMA:
        errors.append(f"expected schema_version {COMMAND_RECEIPT_SCHEMA}")

    status = str(receipt.get("status") or "")
    if status != "fail":
        errors.append(f"receipt status is {status or 'missing'}; expected fail")

    evidence = receipt.get("evidence")
    if not isinstance(evidence, list):
        errors.append("receipt evidence must be a list")
        evidence = []
    elif not evidence:
        errors.append("receipt must include at least one evidence file")

    for item in evidence:
        if not isinstance(item, dict):
            errors.append("evidence item is not an object")
            continue

        path_text = str(item.get("path") or "")
        expected_size = item.get("size_bytes")
        expected_sha = str(item.get("sha256") or "")
        if not path_text:
            errors.append("evidence item is missing a path")
            continue

        path = resolve_evidence_path(base_dir, path_text)
        if not path.exists():
            errors.append(f"evidence file is missing: {path_text}")
            continue
        if not path.is_file():
            errors.append(f"evidence path is not a file: {path_text}")
            continue

        actual_size = path.stat().st_size
        actual_sha = sha256_file(path)
        if actual_size == 0:
            errors.append(f"evidence file is empty: {path_text}")
        if expected_size != actual_size:
            errors.append(
                f"evidence size changed for {path_text}: "
                f"expected {expected_size}, got {actual_size}"
            )
        if expected_sha != actual_sha:
            errors.append(f"evidence hash changed for {path_text}")

        checked_paths.append(Path(path_text).as_posix())
        log_parts.append(path.read_text(encoding="utf-8", errors="replace"))

    if errors:
        raise ValueError("invalid command receipt: " + "; ".join(errors))

    exit_code = receipt.get("exit_code")
    return "\n".join(log_parts), CommandReceiptEvidence(
        path=receipt_path.as_posix(),
        command=str(receipt.get("command") or ""),
        status=status,
        exit_code=exit_code if isinstance(exit_code, int) else None,
        evidence_files=checked_paths,
    )


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


def build_packet(
    log_text: str,
    title: str,
    command_receipt: CommandReceiptEvidence | None = None,
) -> FailurePacket:
    lines = log_text.splitlines()
    commands = extract_commands(lines)
    if command_receipt and command_receipt.command:
        commands = unique([command_receipt.command] + commands, 8)
    errors = extract_errors(lines)
    refs = extract_file_refs(lines)
    summaries = extract_test_summaries(lines)
    checks = suggest_checks(commands, refs)
    return FailurePacket(
        schema_version="agent-ci-failure-packet.v1",
        title=title,
        command_receipt=command_receipt,
        failing_commands=commands,
        error_signals=errors,
        referenced_files=refs,
        test_summaries=summaries,
        suggested_checks=checks,
        next_agent_prompt=build_prompt(title, errors, checks),
    )


def render_markdown(packet: FailurePacket) -> str:
    lines = [f"# CI Failure Packet: {packet.title}", ""]

    if packet.command_receipt:
        receipt = packet.command_receipt
        evidence_files = ", ".join(f"`{path}`" for path in receipt.evidence_files)
        lines.extend([
            "## Command Receipt",
            "",
            f"- Receipt: `{receipt.path}`",
            f"- Status: `{receipt.status}`",
            f"- Exit code: `{receipt.exit_code}`",
            f"- Verified evidence files: {evidence_files}",
            "",
        ])

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
    parser.add_argument("log", nargs="?", help="Path to CI log, or '-' to read from stdin.")
    parser.add_argument("--title", default="CI failure", help="Packet title.")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--output", help="Write output to this path instead of stdout.")
    parser.add_argument(
        "--receipt",
        help="Read a failed agent-command-receipt.v1 JSON file and verify its evidence.",
    )
    parser.add_argument(
        "--receipt-base-dir",
        default=".",
        help="Base directory used to resolve relative receipt evidence paths.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.log and args.receipt:
            print("Use either a log path or --receipt, not both.", file=sys.stderr)
            return 2
        if args.receipt:
            log_text, command_receipt = read_failed_receipt(
                Path(args.receipt),
                Path(args.receipt_base_dir),
            )
        elif args.log:
            log_text = read_log(args.log)
            command_receipt = None
        else:
            print("Either a log path or --receipt is required.", file=sys.stderr)
            return 2

        if not log_text.strip():
            print("No CI log content provided.", file=sys.stderr)
            return 2

        packet = build_packet(log_text, args.title, command_receipt=command_receipt)
        output = json.dumps(asdict(packet), indent=2) if args.format == "json" else render_markdown(packet)

        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(output if output.endswith("\n") else output + "\n", encoding="utf-8")
            print(output_path)
        else:
            print(output, end="" if output.endswith("\n") else "\n")
        return 0
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"agent-ci-failure-packet: {exc}", file=sys.stderr)
        return 2
