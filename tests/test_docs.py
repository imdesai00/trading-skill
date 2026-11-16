import json
import os
import re
import unittest

from _load import ROOT

DOCS = os.path.join(ROOT, "docs")
FILES = ["README.md", "HOW_TO_USE.md", "GETTING_STARTED.md", "DAILY_ROUTINE.md", "SKILLS.md", "SAFETY.md", "CONFIG.md", "DATA_FILES.md",
         "NOTION.md", "GO_LIVE.md", "ARCHITECTURE.md", "TROUBLESHOOTING.md", "GLOSSARY.md", "AUDIT.md", "ROADMAP.md", "PHASE_2_UI.md", "PHASE_3_DHAN.md", "DHAN.md", "FYERS.md"]


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class Docs(unittest.TestCase):
    def test_every_doc_exists_and_is_listed_in_the_index(self):
        index = read("docs", "README.md")
        for f in FILES:
            self.assertTrue(os.path.isfile(os.path.join(DOCS, f)), f)
            if f != "README.md":
                self.assertIn("(%s)" % f, index, f)
        extra = sorted(set(os.listdir(DOCS)) - set(FILES))
        self.assertEqual(extra, [])

    def test_relative_links_resolve(self):
        for f in FILES + ["../README.md"]:
            base = os.path.dirname(os.path.join(DOCS, f))
            for m in re.finditer(r"\]\(([^)#\s]+?)(?:#[^)]*)?\)", read("docs", f)):
                target = m.group(1)
                if target.startswith(("http", "mailto")):
                    continue
                self.assertTrue(os.path.exists(os.path.normpath(os.path.join(base, target))), "%s -> %s" % (f, target))

    def test_every_skill_is_described(self):
        text = read("docs", "SKILLS.md")
        for name in sorted(d for d in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, d, "SKILL.md"))):
            self.assertIn(name, text, name)
        self.assertIn("The 15 skills", text)

    def test_every_config_key_is_documented(self):
        text = read("docs", "CONFIG.md")
        cfg = json.loads(read("trader-budget", "config.example.json"))
        for key in cfg:
            self.assertIn("`%s`" % key, text, key)
        for key in cfg["limits_pct"]:
            self.assertIn(key, text, key)

    def test_every_table_is_in_the_architecture_page(self):
        import sqlite3
        import tempfile
        import shutil
        from _load import load
        db = load("trader-db", "db")
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "x.db")
            db.init(p)
            conn = sqlite3.connect(p)
            names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            conn.close()
        finally:
            shutil.rmtree(d)
        text = read("docs", "ARCHITECTURE.md")
        for n in names:
            self.assertIn("`%s`" % n, text, n)

    def test_every_protection_file_is_in_data_files(self):
        text = read("docs", "DATA_FILES.md")
        for f in ("surveillance.json", "mis.json", "bands.json", "market.json", "results_today.json", "events.json"):
            self.assertIn(f, text, f)

    def test_the_safety_page_states_the_rules(self):
        text = " ".join(read("docs", "SAFETY.md").split())
        for want in ("No tool ever changes `mode`, `broker`, `dhan_enabled`, `fyers_enabled` or `kite_enabled`", "Your typed \"yes\" for that one order", "Allow once",
                     "Nothing from a background or scheduled task", "trader/STOP", "0.3%", "Most individual intraday traders lose money",
                     "TOTP", "including Claude"):
            self.assertIn(want, text, want)

    def test_the_getting_started_page_has_the_security_reminders(self):
        text = read("docs", "GETTING_STARTED.md")
        self.assertIn("TOTP", text)
        self.assertIn("including Claude", text)

    def test_the_roadmap_orders_the_phases_and_keeps_the_rules(self):
        text = " ".join(read("docs", "ROADMAP.md").split())
        self.assertLess(text.index("| 1 | Paper trading"), text.index("PHASE_2_UI.md"))
        self.assertLess(text.index("PHASE_2_UI.md"), text.index("PHASE_3_DHAN.md"))
        for want in ("Exit test for Phase 1", "Paper is the default", "typed yes", "No secrets"):
            self.assertIn(want, text, want)
        ui = " ".join(read("docs", "PHASE_2_UI.md").split())
        for want in ("127.0.0.1", "read-only", "No order endpoints exist", "never decide"):
            self.assertIn(want, ui.replace("Principle: look and plan, never decide", "never decide"), want)
        z = " ".join(read("docs", "PHASE_3_DHAN.md").split())
        for want in ("Not financial advice", "Rollback", "holds **no credentials**", "Needs approval", "dhan_enabled", "FakeDhan"):
            self.assertIn(want, z, want)

    def test_every_command_line_in_the_docs_names_a_real_script(self):
        scripts = set()
        for d in os.listdir(ROOT):
            if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")):
                scripts.update(f for f in os.listdir(os.path.join(ROOT, d)) if f.endswith(".py"))
        for f in FILES:
            for m in re.finditer(r"\b([a-z_]+\.py)\b", read("docs", f)):
                name = m.group(1)
                if name in ("sec_list.py", "ui.py") or name.startswith("test_"):
                    continue
                self.assertIn(name, scripts | {"package.py", "capture.py"}, "%s mentions %s" % (f, name))


if __name__ == "__main__":
    unittest.main()
