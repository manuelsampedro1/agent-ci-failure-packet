import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from agent_ci_failure_packet.cli import build_packet, main


SAMPLE_LOG = """Run make test
+ make test
FAIL: test_blocks_unexpected_paths (tests.test_publish_guard.PublishGuardTests)
Traceback (most recent call last):
  File "tests/test_publish_guard.py", line 42, in test_blocks_unexpected_paths
    self.assertEqual(exit_code, 1)
AssertionError: 0 != 1

Ran 8 tests in 0.032s
FAILED (failures=1)
Process completed with exit code 1.
"""


class FailurePacketTests(unittest.TestCase):
    def write_receipt(
        self,
        directory: str,
        evidence_name: str = "ci.log",
        status: str = "fail",
    ) -> Path:
        evidence = Path(directory) / evidence_name
        evidence.write_text(SAMPLE_LOG, encoding="utf-8")
        receipt_path = Path(directory) / "receipt.json"
        receipt_path.write_text(
            json.dumps({
                "schema_version": "agent-command-receipt.v1",
                "command": "make test",
                "status": status,
                "exit_code": 1 if status == "fail" else 0,
                "cwd": ".",
                "created_at": "2026-06-03T00:00:00Z",
                "notes": [],
                "evidence": [{
                    "path": evidence_name,
                    "size_bytes": evidence.stat().st_size,
                    "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                }],
            }),
            encoding="utf-8",
        )
        return receipt_path

    def test_build_packet_extracts_signals(self) -> None:
        packet = build_packet(SAMPLE_LOG, "Publish guard")

        self.assertIsNone(packet.command_receipt)
        self.assertIn("make test", packet.failing_commands)
        self.assertTrue(any("AssertionError" in error for error in packet.error_signals))
        self.assertEqual(packet.referenced_files[0].path, "tests/test_publish_guard.py")
        self.assertEqual(packet.referenced_files[0].line, 42)
        self.assertTrue(any("FAILED" in summary for summary in packet.test_summaries))
        self.assertIn("make test", packet.suggested_checks)

    def test_cli_json_writes_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "ci.log"
            output_path = Path(tmp) / "packet.json"
            log_path.write_text(SAMPLE_LOG, encoding="utf-8")

            with redirect_stdout(StringIO()):
                exit_code = main([
                    str(log_path),
                    "--title",
                    "Publish guard",
                    "--format",
                    "json",
                    "--output",
                    str(output_path),
                ])

            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["schema_version"], "agent-ci-failure-packet.v1")
        self.assertEqual(payload["title"], "Publish guard")
        self.assertIsNone(payload["command_receipt"])

    def test_cli_builds_packet_from_failed_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            receipt_path = self.write_receipt(tmp)
            output_path = Path(tmp) / "packet.json"

            with redirect_stdout(StringIO()):
                exit_code = main([
                    "--receipt",
                    str(receipt_path),
                    "--receipt-base-dir",
                    tmp,
                    "--title",
                    "Receipt-backed CI failure",
                    "--format",
                    "json",
                    "--output",
                    str(output_path),
                ])

            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["command_receipt"]["status"], "fail")
        self.assertEqual(payload["command_receipt"]["evidence_files"], ["ci.log"])
        self.assertIn("make test", payload["failing_commands"])
        self.assertTrue(any("AssertionError" in error for error in payload["error_signals"]))

    def test_cli_rejects_non_failed_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            receipt_path = self.write_receipt(tmp, status="pass")
            stderr = StringIO()

            with redirect_stderr(stderr):
                exit_code = main([
                    "--receipt",
                    str(receipt_path),
                    "--receipt-base-dir",
                    tmp,
                ])

        self.assertEqual(exit_code, 2)
        self.assertIn("expected fail", stderr.getvalue())

    def test_cli_rejects_receipt_evidence_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            receipt_path = self.write_receipt(tmp)
            Path(tmp, "ci.log").write_text("changed log\n", encoding="utf-8")
            stderr = StringIO()

            with redirect_stderr(stderr):
                exit_code = main([
                    "--receipt",
                    str(receipt_path),
                    "--receipt-base-dir",
                    tmp,
                ])

        self.assertEqual(exit_code, 2)
        self.assertIn("evidence hash changed", stderr.getvalue())

    def test_empty_log_returns_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "empty.log"
            log_path.write_text("", encoding="utf-8")

            with redirect_stderr(StringIO()):
                exit_code = main([str(log_path)])

        self.assertEqual(exit_code, 2)

    def test_missing_log_or_receipt_returns_usage_error(self) -> None:
        with redirect_stderr(StringIO()) as stderr:
            exit_code = main([])

        self.assertEqual(exit_code, 2)
        self.assertIn("Either a log path or --receipt is required", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
