"""Command-line entry point: `qa-agent <command>`."""

from __future__ import annotations

import argparse
import sys

from pydantic import BaseModel

from .config import Settings
from .llm import LLM, CostLedger, LLMError
from .run_context import RunContext


class PingReply(BaseModel):
    ok: bool
    echo: str


def cmd_ping(args: argparse.Namespace) -> int:
    """Smoke test: one structured model call, logged with tokens and cost."""
    settings = Settings()
    run = RunContext.create(settings.runs_dir)
    ledger = CostLedger(run)
    llm = LLM(settings, ledger)
    try:
        result = llm.generate(
            "ping",
            'Reply with ok=true and echo="DemoBank QA agent online".',
            schema=PingReply,
        )
    except LLMError as exc:
        print(f"Model call failed: {exc}", file=sys.stderr)
        return 1
    summary = ledger.summary()
    run.write_json("cost.json", summary)
    r = result.record
    print(f"Reply: {result.parsed.model_dump()}")
    print(
        f"Model: {r.model} | input {r.input_tokens} (cached {r.cached_input_tokens}) | "
        f"output {r.output_tokens} (thinking {r.thinking_tokens}) | ${r.cost_usd:.6f} | {r.latency_s}s"
    )
    print(f"Run artifacts: {run.root}")
    return 0


def _start(args: argparse.Namespace) -> tuple[Settings, RunContext, CostLedger, LLM]:
    settings = Settings()
    if getattr(args, "url", None):
        settings.target_url = args.url
    run = RunContext.create(settings.runs_dir)
    ledger = CostLedger(run)
    return settings, run, ledger, LLM(settings, ledger)


def cmd_interpret(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .evaluation import interpret_recall
    from .interpret import depth_for, interpret

    _settings, run, ledger, llm = _start(args)
    requirements, validation = interpret(llm, Path(args.prd).read_text())
    run.write_json(
        "requirements.json",
        [r.model_dump() | {"depth": depth_for(r)} for r in requirements],
    )
    recall = interpret_recall(requirements)
    run.write_json("interpret_eval.json", {"validation": validation, "recall": recall})
    run.write_json("cost.json", ledger.summary())
    print(f"Requirements kept: {validation['kept']}/{validation['extracted']}  issues: {validation['issues']}")
    print(f"Inventory recall: {recall['covered']}/{recall['inventory_size']} missed: {recall['missed']}")
    print(f"Cost: ${ledger.summary()['total_cost_usd']:.4f}  ->  {run.root}")
    return 0


def cmd_explore(args: argparse.Namespace) -> int:
    from .explore import explore

    settings, run, ledger, llm = _start(args)
    app_map = explore(llm, settings.target_url, repo_path=args.repo, headless=not args.headed)
    run.write_json("app_map.json", app_map)
    run.write_json("cost.json", ledger.summary())
    print(f"Pages: {sorted(app_map['pages'])}")
    print(f"Entry: {app_map['entry'] and {k: v for k, v in app_map['entry'].items() if k != 'network'}}")
    print(f"API: {app_map['api']['api_origin']} openapi={app_map['api']['openapi_found']} "
          f"endpoints={len(app_map['api']['endpoints'])}  repo hints={len(app_map['repo_hints']['hints'])}")
    print(f"Cost: ${ledger.summary()['total_cost_usd']:.4f}  ->  {run.root}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Full pipeline: interpret -> explore -> per-requirement loop -> report."""
    import json
    import time
    from datetime import datetime
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    from .evaluation import interpret_recall
    from .executor import Environment
    from .explore import explore
    from .interpret import depth_for, interpret
    from .orchestrator import RequirementTester
    from .planner import Planner
    from .report import narrative, render_report
    from .schemas import Requirement

    settings, run, ledger, llm = _start(args)
    started, t0 = datetime.now().isoformat(timespec="seconds"), time.monotonic()
    print(f"Run folder: {run.root}")

    prd_text = Path(args.prd).read_text()
    if args.requirements:
        raw = json.loads(Path(args.requirements).read_text())
        requirements = [Requirement.model_validate({k: v for k, v in r.items() if k != "depth"}) for r in raw]
        validation = {"extracted": len(requirements), "kept": len(requirements), "issues": ["reused from file"]}
    else:
        print("INTERPRET: reading the PRD…")
        requirements, validation = interpret(llm, prd_text)
    interpret_eval = {"validation": validation, "recall": interpret_recall(requirements)}
    run.write_json("requirements.json", [r.model_dump() | {"depth": depth_for(r)} for r in requirements])
    run.write_json("interpret_eval.json", interpret_eval)
    print(f"  {len(requirements)} requirements; inventory recall {interpret_eval['recall']['covered']}/"
          f"{interpret_eval['recall']['inventory_size']}")

    if args.app_map:
        app_map = json.loads(Path(args.app_map).read_text())
    else:
        print("EXPLORE: crawling the live app…")
        app_map = explore(llm, settings.target_url, repo_path=args.repo, headless=not args.headed)
    run.write_json("app_map.json", app_map)
    print(f"  routes: {sorted(app_map['pages'])}")

    env = Environment(
        target_url=settings.target_url,
        api_origin=app_map["api"]["api_origin"],
        entry=app_map["entry"],
        database_url=settings.database_url,
        run_dir=run.root,
    )
    environment = (
        "Local target. The runner restores the seeded database before every plan with "
        "needs_clean_state=true and every scenario starts in a fresh browser (no session). Still never "
        "hard-code seed values: read them."
        if env.can_reset
        else "Shared hosted target. State is NOT reset between plans: other tests may have changed balances "
        "and history. Every scenario starts in a fresh browser (no session). Read values before acting and "
        "compare relative to them."
    )
    planner = Planner(llm, app_map, environment)

    selected = [r for r in requirements if not args.only or r.id in args.only.split(",")]
    if args.limit:
        selected = selected[: args.limit]
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed, slow_mo=args.slow_mo)
        tester = RequirementTester(llm, planner, browser, env)
        for req in selected:
            results.append(tester.test(req, depth_for(req)))
            run.write_json("results.json", results)  # incremental: survives interruption
        browser.close()

    context = {
        "target_url": settings.target_url,
        "target_kind": "local copy, database reset between scenarios" if env.can_reset else "hosted",
        "prd_path": args.prd,
        "run_id": run.root.name,
        "started": started,
        "duration_min": round((time.monotonic() - t0) / 60, 1),
        "models": ", ".join(sorted(set(settings.models.values()))),
        "interpret_eval": interpret_eval,
    }
    print("REPORT: writing the audit report…")
    story, removed = narrative(llm, results, context)
    cost = ledger.summary()  # after the narrative call, so the report's cost includes it
    run.write_json("cost.json", cost)
    report_md = render_report(results, context, cost, story, removed)
    run.path("report.md").write_text(report_md)
    counts = {v: sum(r["verdict"] == v for r in results) for v in ("PASS", "FAIL", "UNVERIFIED")}
    print(f"DONE: {counts}  cost ${cost['total_cost_usd']:.4f}  ->  {run.path('report.md')}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Rebuild report.md for an existing run folder from its saved results (adds one narrative call)."""
    import json
    from pathlib import Path

    from .llm import CallRecord
    from .report import narrative, render_report

    root = Path(args.run)
    settings = Settings()
    run = RunContext(root=root)
    ledger = CostLedger()  # replay the run's own calls so the cost table covers the whole run
    calls_file = root / "llm_calls.jsonl"
    if calls_file.exists():
        for line in calls_file.read_text().splitlines():
            ledger.calls.append(CallRecord(**json.loads(line)))
    ledger.run = run  # new calls (the narrative) are appended to the run's log
    llm = LLM(settings, ledger)
    results = json.loads((root / "results.json").read_text())
    interpret_eval = json.loads((root / "interpret_eval.json").read_text()) if (root / "interpret_eval.json").exists() else {}
    app_map = json.loads((root / "app_map.json").read_text())
    shots = sorted((root / "screenshots").glob("*.jpg"), key=lambda p: p.stat().st_mtime)
    duration = round((shots[-1].stat().st_mtime - shots[0].stat().st_mtime) / 60, 1) if len(shots) > 1 else "n/a"
    context = {
        "target_url": app_map["target_url"],
        "target_kind": args.target_kind,
        "prd_path": args.prd,
        "run_id": root.name,
        "started": root.name,
        "duration_min": duration,
        "models": ", ".join(sorted(set(settings.models.values()))),
        "interpret_eval": interpret_eval,
    }
    story, removed = narrative(llm, results, context)
    cost = ledger.summary()
    run.write_json("cost.json", cost)
    (root / "report.md").write_text(render_report(results, context, cost, story, removed))
    print(f"Report rebuilt: {root / 'report.md'}  (total run cost ${cost['total_cost_usd']:.4f})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qa-agent", description="Autonomous PRD-driven QA agent")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ping", help="one test model call with cost logging").set_defaults(func=cmd_ping)

    p = sub.add_parser("interpret", help="PRD -> requirements.json")
    p.add_argument("--prd", required=True)
    p.set_defaults(func=cmd_interpret)

    p = sub.add_parser("explore", help="crawl the live app -> app_map.json")
    p.add_argument("--url")
    p.add_argument("--repo", help="optional path to the target's source (fills gaps only)")
    p.add_argument("--headed", action="store_true")
    p.set_defaults(func=cmd_explore)

    p = sub.add_parser("run", help="full QA run: PRD + live app -> report.md")
    p.add_argument("--prd", required=True)
    p.add_argument("--url")
    p.add_argument("--repo", help="optional path to the target's source (fills gaps only)")
    p.add_argument("--only", help="comma-separated requirement ids to test")
    p.add_argument("--limit", type=int, help="test only the first N requirements")
    p.add_argument("--requirements", help="reuse a requirements.json instead of re-interpreting")
    p.add_argument("--app-map", help="reuse an app_map.json instead of re-exploring")
    p.add_argument("--headed", action="store_true", help="show the browser (for demos)")
    p.add_argument("--slow-mo", type=int, default=0, help="ms delay between browser actions (demos)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("report", help="rebuild report.md for an existing run folder")
    p.add_argument("--run", required=True, help="path to runs/<timestamp>")
    p.add_argument("--prd", default="../test-website/PRD.md")
    p.add_argument("--target-kind", default="local copy, database reset between scenarios")
    p.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
