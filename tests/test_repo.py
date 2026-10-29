import json
import os
import re
import unittest

from _load import ROOT

# v2, the day-trading pack: the eleven folders the reel's repo card shows
SKILLS = ["trader-scanner", "trader-news", "trader-entry", "trader-exit", "trader-timemachine",
          "trader-risk", "trader-budget", "trader-broker", "trader-alerts", "trader-analyst",
          "trader-pelosi"]
ABOUT = ("11 Claude skills for day trading: scan thousands of charts, track breaking news, time every "
         "entry and exit, and replay every outcome before you risk a dollar.")
# skills on the way to an order carry the safety block
ORDER_PATH = ["trader-scanner", "trader-entry", "trader-exit", "trader-timemachine", "trader-budget",
              "trader-pelosi", "trader-risk", "trader-broker", "trader-alerts"]
V1_ONLY = ["trader-copycat", "trader-screener", "trader-tax", "trader-rebalance"]


def frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1) if m else ""), text


def every_text_file():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for f in files:
            if f.endswith((".md", ".py", ".json", ".csv", ".txt", ".xml")) or f == "LICENSE":
                yield os.path.join(base, f)


class Repo(unittest.TestCase):
    def test_eleven_skill_folders_at_the_root(self):
        found = sorted(d for d in os.listdir(ROOT)
                       if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))
        self.assertEqual(found, sorted(SKILLS))

    def test_root_matches_the_reel(self):
        root = sorted(d for d in os.listdir(ROOT) if not d.startswith(".git") and d != "__pycache__")
        self.assertEqual(root, sorted([".claude-plugin", "LICENSE", "PLAN.md", "README.md", "VERIFY.md", "tests"] + SKILLS))

    def test_frontmatter_name_and_description(self):
        for s in SKILLS:
            fm, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertIn("name: %s\n" % s, fm + "\n", s)
            self.assertRegex(fm, r"description: ", s)
            self.assertGreater(len(fm), 200, "%s description is too thin" % s)
            self.assertGreater(len(text.splitlines()), 60, s)

    def test_order_path_skills_carry_the_safety_rule(self):
        for s in ORDER_PATH:
            _, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            low = " ".join(text.lower().split())
            self.assertIn("not financial advice", low, s)
            self.assertIn("yes", low, s)

    def test_plugin_manifest_points_at_the_root_skills(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as fh:
            plugin = json.load(fh)
        self.assertEqual(plugin["name"], "day-trader-skill")
        self.assertEqual(plugin["skills"], "./")
        self.assertEqual(plugin["description"], ABOUT)
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")) as fh:
            market = json.load(fh)
        self.assertEqual(market["name"], "day-trader-skill")
        self.assertEqual(market["plugins"][0]["name"], "day-trader-skill")

    def test_readme_carries_the_reel_copy(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = " ".join(fh.read().split())
        self.assertTrue(readme.startswith("# The Day Trader skill"))
        for line in (ABOUT,
                     "https://github.com/Jakeschincariol/day-trader-skill install skill",
                     "/plugin marketplace add Jakeschincariol/day-trader-skill",
                     "/plugin install day-trader-skill@day-trader-skill",
                     "## Fine print"):
            self.assertIn(line, readme)
        for s in SKILLS:
            self.assertIn("/" + s, readme, s)

    def test_no_em_dashes_and_no_personal_email(self):
        for path in every_text_file():
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            rel = os.path.relpath(path, ROOT)
            if not rel.startswith(os.path.join("tests", "fixtures")):  # fixtures are real data, kept as published
                self.assertNotIn("\u2014", text, rel)
            self.assertNotIn("jakeschincariol" + "@" + "gmail.com", text, rel)  # built, so this file passes too

    def test_no_v1_only_skill_is_left_behind(self):
        for path in every_text_file():
            rel = os.path.relpath(path, ROOT)
            if rel.startswith("tests" + os.sep) or rel == "README.md":
                continue  # the README points v1 users to the v1 commit
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for old in V1_ONLY:
                self.assertNotIn(old, text, rel)

    def test_every_tool_runs_on_python_3_8(self):
        # no 3.9+ only modules or syntax in the tools (the README promises 3.8)
        for s in SKILLS:
            folder = os.path.join(ROOT, s)
            for f in os.listdir(folder):
                if not f.endswith(".py"):
                    continue
                with open(os.path.join(folder, f), encoding="utf-8") as fh:
                    src = fh.read()
                for bad in ("import zoneinfo", "from zoneinfo", ".removeprefix(", ".removesuffix(",
                            "match ", "list[", "dict[", "tuple["):
                    if bad == "match ":
                        self.assertNotRegex(src, r"(?m)^\s+match \w+.*:$", "%s/%s" % (s, f))
                    elif bad.endswith("["):
                        self.assertNotRegex(src, r"(?<![\w.])%s" % re.escape(bad), "%s/%s" % (s, f))
                    else:
                        self.assertNotIn(bad, src, "%s/%s" % (s, f))

    def test_day_trading_limits_ship_in_the_example_config(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
            cfg = json.load(fh)
        want = {"capital_inr": 100000, "max_loss_per_trade_inr": 500, "max_daily_loss_inr": 1500,
                "max_weekly_loss_inr": 4000, "max_drawdown_pause_inr": 8000, "max_trades_per_day": 3,
                "max_open_positions": 1, "max_position_value_inr": 75000, "cash_buffer_pct": 10,
                "min_reward_risk": 1.5, "max_charges_pct_of_target": 25, "allow_margin": False,
                "allow_short": False, "no_entries_before": "09:30", "no_entries_after": "14:45",
                "flat_by": "15:00", "paper_slippage_pct": 0.05, "paper_period_weeks": 4,
                "min_trades_for_review": 40, "require_stop": True, "product": "MIS", "exchange": "NSE"}
        for k, v in want.items():
            self.assertEqual(cfg.get(k), v, k)

    def test_paper_trading_is_the_default(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
            cfg = json.load(fh)
        self.assertEqual(cfg["mode"], "paper")
        self.assertIs(cfg["kite_enabled"], False)


class Fixtures(unittest.TestCase):
    def test_the_synthetic_fixtures_rebuild_byte_for_byte(self):
        import importlib.util
        import shutil
        import tempfile
        src = os.path.join(ROOT, "tests", "fixtures", "nse")
        d = tempfile.mkdtemp()
        try:
            shutil.copy(os.path.join(src, "make_synthetic.py"), d)
            spec = importlib.util.spec_from_file_location("make_synthetic", os.path.join(d, "make_synthetic.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for path in mod.build():
                rel = os.path.relpath(path, d)
                with open(path, "rb") as a, open(os.path.join(src, rel), "rb") as b:
                    self.assertEqual(a.read(), b.read(), rel)
        finally:
            shutil.rmtree(d)

    def test_every_synthetic_chart_says_so(self):
        import gzip
        for base, _, files in os.walk(os.path.join(ROOT, "tests", "fixtures", "nse")):
            for f in files:
                if f.endswith((".json", ".json.gz")):
                    opener = gzip.open if f.endswith(".gz") else open
                    with opener(os.path.join(base, f), "rt", encoding="utf-8") as fh:
                        meta = json.load(fh)["chart"]["result"][0]["meta"]
                    self.assertIs(meta.get("synthetic"), True, f)
                    self.assertTrue(meta["symbol"].startswith("SYNTH"), f)

    def test_capture_writes_the_layout(self):
        import importlib.util
        import tempfile
        import shutil
        spec = importlib.util.spec_from_file_location("capture", os.path.join(ROOT, "tests", "capture.py"))
        cap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cap)
        d = tempfile.mkdtemp()
        try:
            urls = []
            done = cap.capture(["M&M"], d, get=lambda u: urls.append(u) or b"{}", sleep=lambda s: None)
            self.assertEqual(len(done), 3)
            self.assertIn("/chart/M%26M.NS?range=60d&interval=5m", urls[0])
            self.assertTrue(os.path.exists(os.path.join(d, "1m-5d", "M&M.json.gz")))
            with open(os.path.join(d, "CAPTURED.txt")) as fh:
                self.assertEqual(len(fh.read().splitlines()), 3)
        finally:
            shutil.rmtree(d)


class Secrets(unittest.TestCase):
    def test_gitignore_keeps_state_and_secrets_out(self):
        with open(os.path.join(ROOT, ".gitignore")) as fh:
            lines = fh.read().splitlines()
        for want in ("trader/", ".env", "claude_desktop_config.json", "__pycache__/"):
            self.assertIn(want, lines)

    def test_no_file_looks_like_it_holds_a_key(self):
        pat = re.compile(r"(api_secret|access_token|api_key)\s*[=:]\s*['\"][A-Za-z0-9]{12,}['\"]", re.I)
        for path in every_text_file():
            with open(path, encoding="utf-8", errors="replace") as fh:
                self.assertIsNone(pat.search(fh.read()), os.path.relpath(path, ROOT))


class Packaging(unittest.TestCase):
    def test_every_converted_skill_packages_in_the_layout_claude_wants(self):
        import importlib.util
        import tempfile
        import shutil
        import zipfile
        spec = importlib.util.spec_from_file_location("package", os.path.join(ROOT, "tests", "package.py"))
        pkg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pkg)
        d = tempfile.mkdtemp()
        try:
            for name in pkg.CONVERTED:
                self.assertEqual(pkg.check_skill(name), [], name)
                z = pkg.build(name, d)
                with zipfile.ZipFile(z) as zf:
                    names = zf.namelist()
                self.assertIn(name + "/SKILL.md", names)
                self.assertTrue(all(n.startswith(name + "/") for n in names), name)
                self.assertFalse(any("__pycache__" in n or n.endswith(".pyc") for n in names), name)
            a, b = pkg.build("trader-risk", d), os.path.join(d, "again")
            with open(a, "rb") as fh:
                first = fh.read()
            self.assertEqual(first, open(pkg.build("trader-risk", b), "rb").read())   # repeatable
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
