import glob
import os
import subprocess
import sys
import tempfile
import unittest

from _load import ROOT


class WindowsConsole(unittest.TestCase):
    """A Windows console defaults to cp1252, which cannot print the rupee sign. Every script must still run."""

    def test_every_script_forces_utf8_output(self):
        for f in glob.glob(os.path.join(ROOT, "trader-*", "*.py")):
            if os.path.basename(f) in ("db.py", "charges.py", "guards.py"):
                continue
            with open(f, encoding="utf-8") as fh:
                text = fh.read()
            if '__name__ == "__main__"' in text:
                self.assertIn('reconfigure(encoding="utf-8"', text, f)

    def test_init_and_morning_check_survive_a_cp1252_console(self):
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        env.pop("PYTHONUTF8", None)
        with tempfile.TemporaryDirectory() as d:
            for rel in ("trader-budget/size.py init", "trader-control/control.py morning"):
                parts = rel.split()
                p = subprocess.run([sys.executable, os.path.join(ROOT, parts[0])] + parts[1:], cwd=d, env=env,
                                   capture_output=True)
                self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
                self.assertNotIn(b"UnicodeEncodeError", p.stderr)


if __name__ == "__main__":
    unittest.main()
