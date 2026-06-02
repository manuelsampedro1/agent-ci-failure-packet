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
    def test_build_packet_extracts_signals(self) -> None:
        packet = build_packet(SAMPLE_LOG, "Publish guard")

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

    def test_empty_log_returns_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "empty.log"
            log_path.write_text("", encoding="utf-8")

            with redirect_stderr(StringIO()):
                exit_code = main([str(log_path)])

        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
