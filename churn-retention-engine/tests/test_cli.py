import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from churn_engine.cli import main

from .helpers import ROOT, standard_rows, write_csv

CONFIG = str(ROOT / "config" / "scoring.toml")


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def invoke(self, rows, *extra):
        data = write_csv(self.tmp / "data.csv", rows)
        args = ["--input", str(data), "--config", CONFIG, "--output-dir", str(self.tmp / "out"),
                "--as-of", "2026-10-16T12:00:00Z", "--no-state", "--no-color", "--log-level", "ERROR", *extra]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(args)
        return code, out.getvalue()

    def test_dry_run_prints_the_alert_table(self):
        code, text = self.invoke(standard_rows())
        self.assertEqual(code, 0)
        self.assertIn("simulación", text)
        self.assertIn("X-3", text)
        self.assertIn("simulada", text)

    def test_dispatch_without_url_is_a_fatal_error(self):
        with mock.patch.dict(os.environ, {"MAKE_WEBHOOK_URL": ""}):
            code, _ = self.invoke(standard_rows(), "--dispatch")
        self.assertEqual(code, 1)

    def test_guardrail_returns_exit_code_3_without_sending(self):
        rows = standard_rows(n_healthy=10, n_critical=10)
        code, text = self.invoke(rows, "--dispatch", "--webhook-url", "https://hook.example.invalid/abc")
        self.assertEqual(code, 3)
        self.assertIn("guardrail", text)
        self.assertIn("bloqueada", text)

    def test_missing_input_is_a_fatal_error(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--input", str(self.tmp / "no_existe.csv"), "--config", CONFIG,
                         "--no-state", "--log-level", "ERROR"])
        self.assertEqual(code, 1)

    def test_invalid_as_of_is_rejected_by_the_parser(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            main(["--as-of", "mañana"])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
