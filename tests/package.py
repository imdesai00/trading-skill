#!/usr/bin/env python3
"""Build one uploadable ZIP per skill (Claude app: Customize > Skills > + > Upload a skill).

    python3 tests/package.py                 # -> dist/<skill>.zip for every converted skill
    python3 tests/package.py trader-risk     # just one

Each ZIP holds ONE folder (the skill's own name) with SKILL.md and its scripts at the top, which is the
layout Claude's skill upload wants. Every skill is standalone: no script reaches into a sibling folder
(tests/test_clock.py checks that). The ZIP is byte-for-byte repeatable.
Skills not converted to NSE yet (news, analyst, pelosi) are left out until their phase.
"""

import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONVERTED = ["trader-scanner", "trader-entry", "trader-exit", "trader-timemachine", "trader-budget",
             "trader-risk", "trader-broker", "trader-alerts"]
SKIP = ("__pycache__",)


def check_skill(name):
    """Problems that would stop Claude from loading the skill (empty list = fine)."""
    path = os.path.join(ROOT, name, "SKILL.md")
    if not os.path.exists(path):
        return ["no SKILL.md"]
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return ["no frontmatter"]
    fm, bad = m.group(1), []
    n = re.search(r"^name: (.+)$", fm, re.M)
    if not n or n.group(1).strip() != name:
        bad.append("name must be %r" % name)
    if not re.match(r"^[a-z0-9-]{1,64}$", name) or "claude" in name or "anthropic" in name:
        bad.append("name not allowed")
    d = re.search(r"description: >-\n((?:  .*\n?)+)", fm + "\n")
    desc = " ".join(x.strip() for x in d.group(1).splitlines()) if d else ""
    if not desc or len(desc) > 1024:
        bad.append("description is %d characters (1 to 1024)" % len(desc))
    return bad


def build(name, out_dir):
    problems = check_skill(name)
    if problems:
        raise SystemExit("%s: %s" % (name, "; ".join(problems)))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name + ".zip")
    src = os.path.join(ROOT, name)
    files = []
    for base, dirs, names in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in SKIP)
        files += [os.path.join(base, f) for f in sorted(names) if not f.endswith(".pyc")]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            info = zipfile.ZipInfo(os.path.join(name, os.path.relpath(f, src)), (2026, 1, 1, 0, 0, 0))
            info.external_attr = (0o755 if f.endswith(".py") else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(f, "rb") as fh:
                z.writestr(info, fh.read())
    return path


def main(argv=None):
    names = (argv if argv is not None else sys.argv[1:]) or CONVERTED
    for n in names:
        p = build(n, os.path.join(ROOT, "dist"))
        print("%-20s %6.1f KB  %s" % (n, os.path.getsize(p) / 1024.0, os.path.relpath(p, ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
