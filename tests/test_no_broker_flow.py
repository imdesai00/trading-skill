import os
import subprocess
import sys
import tempfile
import unittest

from _load import ROOT

F = os.path.join(ROOT, "tests", "fixtures", "nse")
NOW = "2026-10-30 09:50"


class NoBrokerPaperMonth(unittest.TestCase):
    """One whole paper trade from an empty folder, with no broker, config switches all off, on synthetic bars."""

    def run_tool(self, cwd, script, *args, ok=0):
        p = subprocess.run([sys.executable, "-I", os.path.join(ROOT, script)] + list(args), cwd=cwd, capture_output=True,
                           text=True, encoding="utf-8")
        self.assertEqual(p.returncode, ok, "%s %s\n%s\n%s" % (script, args, p.stdout, p.stderr))
        return p.stdout

    def test_scan_to_report(self):
        with tempfile.TemporaryDirectory() as d:
            t = self.run_tool
            t(d, "trader-budget/size.py", "init")
            self.assertIn("intact", t(d, "trader-db/db.py", "check"))
            self.assertIn("TRIGGERED", t(d, "trader-entry/entry.py", "check", "SYNTHA", "--offline", F + "/5m-1d", "--now", NOW, "--write"))
            t(d, "trader-exit/exit.py", "plan", "--from", "trader/entry.json", "--offline", F + "/5m-1d", "--now", NOW, "--target", "3R", "--write")
            self.assertIn("BUY 45 SYNTHA", t(d, "trader-budget/size.py", "--plan", "trader/plan.json", "--now", NOW, "--write"))
            self.assertIn("1 passed, 0 blocked", t(d, "trader-risk/check.py", "--now", NOW))
            self.assertIn("PAPER: no broker is contacted", t(d, "trader-broker/ticket.py", "show", "--now", NOW))
            t(d, "trader-broker/ticket.py", "approve", "--index", "1", "--reply", "yes", "--why", "test", "--now", "2026-10-30 09:51")
            self.assertIn("No broker was contacted", t(d, "trader-broker/paper.py", "open", "--index", "1", "--now", "2026-10-30 09:51"))
            self.assertIn("PT-20261030-01", t(d, "trader-broker/paper.py", "reconcile", "--offline", F + "/1m-1d", "--interval", "1m",
                                              "--now", "2026-10-30 15:35", "--date", "2026-10-30"))
            t(d, "trader-broker/paper.py", "feel", "--trade", "PT-20261030-01", "--text", "calm", "--now", "2026-10-30 15:40")
            self.assertIn("how I felt: calm", t(d, "trader-report/report.py", "daily", "--now", "2026-10-30 15:45"))
            self.assertIn("intact", t(d, "trader-db/db.py", "check"))
            self.assertTrue(os.path.isdir(os.path.join(d, "trader", "reports")))

    def test_the_default_two_r_plan_is_refused_after_charges(self):
        with tempfile.TemporaryDirectory() as d:
            t = self.run_tool
            t(d, "trader-budget/size.py", "init")
            t(d, "trader-entry/entry.py", "check", "SYNTHA", "--offline", F + "/5m-1d", "--now", NOW, "--write")
            t(d, "trader-exit/exit.py", "plan", "--from", "trader/entry.json", "--offline", F + "/5m-1d", "--now", NOW, "--write")
            self.assertIn("REFUSED", t(d, "trader-budget/size.py", "--plan", "trader/plan.json", "--now", NOW, "--write", ok=1))


if __name__ == "__main__":
    unittest.main()
