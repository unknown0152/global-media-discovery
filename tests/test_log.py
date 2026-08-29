from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import logging
import unittest

from gmd.log import configure_logging


class LoggingTests(unittest.TestCase):
    def test_structured_logs_do_not_corrupt_command_stdout(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            configure_logging("INFO")
            logging.getLogger("gmd.test").warning("operator-visible warning")

        self.assertEqual(stdout.getvalue(), "")
        self.assertIn('"message":"operator-visible warning"', stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
