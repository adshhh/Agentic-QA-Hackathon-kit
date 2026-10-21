#!/usr/bin/env python3
"""Copy a run folder into sample_run/ for committing, keeping only screenshots the report links to.

Usage: python scripts/publish_run.py runs/<timestamp> [--name <folder name>]
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent
KEEP = ("report.md", "results.json", "requirements.json", "interpret_eval.json", "app_map.json",
        "llm_calls.jsonl", "cost.json")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    parser.add_argument("--name")
    args = parser.parse_args()
    src = Path(args.run)
    dst = AGENT / "sample_run" / (args.name or src.name)
    if dst.exists():
        shutil.rmtree(dst)
    (dst / "screenshots").mkdir(parents=True)
    for name in KEEP:
        if (src / name).exists():
            shutil.copy2(src / name, dst / name)
    report = (src / "report.md").read_text()
    linked = set(re.findall(r"\./screenshots/([^)\s]+\.jpg)", report))
    for shot in linked:
        if (src / "screenshots" / shot).exists():
            shutil.copy2(src / "screenshots" / shot, dst / "screenshots" / shot)
    size = sum(p.stat().st_size for p in dst.rglob("*") if p.is_file()) / 1e6
    print(f"Published {src} -> {dst}: {len(linked)} screenshots, {size:.1f} MB")


if __name__ == "__main__":
    main()
