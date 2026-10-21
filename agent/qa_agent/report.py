"""REPORT: the model writes the narrative from results only; code inserts every fact (D11).

Every table, number, step, observed value and screenshot link below is produced by code from
results.json. The model only writes the summary/findings/limitations prose, and any sentence in
it that cites a number not present in the results is removed before publishing.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from .llm import LLM, LLMError
from .schemas import ReportNarrative

NARRATIVE_SYSTEM = """You write the narrative sections of a QA audit report for developers who did not
watch the test run. Use ONLY the facts in the provided results. Refer to requirements by id. Be
specific and concise: what failed, the concrete observed values that prove it (e.g. balance before
and after, the input used), why it matters to a user, and what to fix. Do not restate every pass.
Mention honest limitations (unverified items and why, lightly-tested areas, local vs hosted target).
Keep each key finding to 1-2 sentences. When stating how many requirements were tested, passed,
failed or unverified, use exactly the numbers in "counts"."""

NUMBER_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?")


def _describe_step(s: dict[str, Any]) -> str:
    a, loc, name, val = s.get("action"), s.get("locator"), s.get("name"), s.get("value")
    target = f"{loc} '{name}'" if name else f"'{loc}'" if loc else ""
    by = s.get("by")
    if by == "testid" and loc:
        target = f"element [data-testid={loc}]"
    elif by == "label" and loc:
        target = f"field labelled '{loc}'"
    text = {
        "start_session": "Open the landing page and enter the demo",
        "goto": f"Navigate to {val}",
        "click": f"Click {target}",
        "fill": f"Type {val!r} into {target}",
        "select": f"Select '{val}' in {target}",
        "press": f"Press {val} in {target}",
        "reload": "Reload the page",
        "back": "Go back",
        "clear_cookies": "Clear browser cookies",
        "set_viewport": f"Resize the viewport to {val}",
        "read_api": f"Read API GET {val}",
        "wait_for_text": f"Wait for text {val!r}",
        "measure_overflow": "Measure horizontal overflow",
    }.get(a) or f"Read {a.removeprefix('read_')} of {target}"
    saved = s.get("saved")
    if saved:
        text += " → " + ", ".join(f"`{k}` = `{json.dumps(v, default=str)[:160]}`" for k, v in saved.items())
    if not s.get("ok", True):
        text += f"  **(step failed: {s.get('error')})**"
    return text


def _final_attempt(scenario: dict[str, Any]) -> dict[str, Any]:
    return scenario["attempts"][-1] if scenario.get("attempts") else {}


def _fact_strings(results: list[dict[str, Any]], extra: dict[str, Any]) -> str:
    return json.dumps({"results": results, "extra": extra}, default=str)


def _strip_unsupported(text: str, facts: str) -> tuple[str, list[str]]:
    """Drop sentences that cite numbers absent from the facts."""
    removed: list[str] = []
    kept: list[str] = []
    norm_facts = facts.replace(",", "")
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        numbers = [n.replace("$", "").replace(",", "") for n in NUMBER_RE.findall(sentence)]
        unsupported = [n for n in numbers if n and n not in norm_facts]
        (removed if unsupported else kept).append(sentence)
    return " ".join(kept), removed


def narrative(llm: LLM, results: list[dict[str, Any]], context: dict[str, Any]) -> tuple[ReportNarrative | None, list[str]]:
    compact = []
    for r in results:
        item = {"id": r["id"], "area": r.get("area"), "depth": r.get("depth"), "verdict": r["verdict"],
                "reason": r["reason"][:400], "statement": r.get("statement"), "prd_quote": r.get("prd_quote")}
        if r["verdict"] == "FAIL":  # give the narrative concrete evidence, not just the verdict
            evidence = []
            for s in r.get("scenarios", []):
                if s["status"] != "FAIL":
                    continue
                runs = _final_attempt(s).get("runs") or []
                for run in [x for x in runs if x["outcome"] == "FAIL"][:3]:
                    evidence.append({
                        "scenario": s["scenario"], "input": run.get("param"),
                        "failed_checks": [{"expected": c["description"],
                                           "observed": json.dumps(c["observed"], default=str)[:300]}
                                          for c in run["checks"] if c["status"] == "fail"][:3],
                    })
            item["evidence"] = evidence[:4]
        compact.append(item)
    counts = dict(Counter(r["verdict"] for r in results)) | {"requirements_tested": len(results)}
    try:
        parsed: ReportNarrative = llm.generate(
            "report", json.dumps({"counts": counts, "context": context, "results": compact}, default=str),
            system=NARRATIVE_SYSTEM, schema=ReportNarrative,
        ).parsed
    except LLMError:
        return None, ["narrative generation failed; report contains code-generated sections only"]
    facts = _fact_strings(results, context)
    removed: list[str] = []
    parsed.executive_summary, r1 = _strip_unsupported(parsed.executive_summary, facts)
    removed += r1
    for field in ("key_findings", "limitations", "recommendations"):
        cleaned = []
        for item in getattr(parsed, field):
            text, r = _strip_unsupported(item, facts)
            removed += r
            if text.strip():
                cleaned.append(text)
        setattr(parsed, field, cleaned)
    return parsed, removed


def render_report(
    results: list[dict[str, Any]],
    context: dict[str, Any],
    cost: dict[str, Any],
    story: ReportNarrative | None,
    removed: list[str],
) -> str:
    counts = Counter(r["verdict"] for r in results)
    lines: list[str] = []
    w = lines.append
    w("# DemoBank QA Audit Report\n")
    w(f"- **Target:** {context['target_url']}  ({context['target_kind']})")
    w(f"- **Specification:** `{context['prd_path']}`")
    w(f"- **Run:** `{context['run_id']}`  ·  started {context['started']}  ·  duration {context['duration_min']} min")
    w(f"- **Models:** {context['models']}  ·  pricing as of {cost['pricing_date']}")
    w(f"- **Requirements tested:** {len(results)}  ·  **PASS {counts['PASS']}**  ·  **FAIL {counts['FAIL']}**"
      f"  ·  **UNVERIFIED {counts['UNVERIFIED']}**")
    w(f"- **Total model cost of this run:** **${cost['total_cost_usd']:.4f}** "
      f"({cost['total_calls']} calls, {cost['total_input_tokens']:,} input / {cost['total_output_tokens']:,} output tokens)\n")
    w("Verdicts follow the evidence model: PASS = observed state matches the PRD; FAIL = evidence shows a "
      "meaningful mismatch that reproduced on a fresh re-run and was confirmed by a majority review against "
      "the PRD sentence; UNVERIFIED = no reliable conclusion, with the reason stated.\n")

    if story:
        w("## Executive summary\n")
        w(story.executive_summary + "\n")
        if story.key_findings:
            w("### Key findings\n")
            lines.extend(f"- {f}" for f in story.key_findings)
            w("")

    w("## Results by requirement\n")
    w("| ID | Area | Depth | Verdict | Scenarios | Reason |")
    w("|---|---|---|---|---|---|")
    icon = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "UNVERIFIED": "⚠️ UNVERIFIED"}
    for r in results:
        sc = r.get("scenarios", [])
        tally = ", ".join(f"{v} {k.lower()}" for k, v in Counter(s["status"] for s in sc).items())
        reason = r["reason"].replace("|", "\\|").replace("\n", " ")[:180]
        w(f"| [{r['id']}](#{r['id'].lower()}) | {r.get('area', '')} | {r.get('depth', '')} | {icon[r['verdict']]} "
          f"| {tally} | {reason} |")
    w("")

    for title, wanted in (("Failures", "FAIL"), ("Unverified requirements", "UNVERIFIED"), ("Passed requirements", "PASS")):
        group = [r for r in results if r["verdict"] == wanted]
        if not group:
            continue
        w(f"## {title}\n")
        for r in group:
            w(f"### {r['id']}\n")
            w(f"**Requirement:** {r.get('statement', '')}  ")
            w(f"**PRD:** “{r.get('prd_quote', '')}”  ")
            w(f"**Verdict:** {icon[r['verdict']]}: {r['reason']}\n")
            for s in r.get("scenarios", []):
                if s["status"] == "PASS":
                    # Passes carry evidence too, folded so failures stay prominent.
                    w(f"<details><summary>✅ <i>{s['scenario']}</i> ({s['scenario_type']}, {s['technique']})"
                      "</summary>\n")
                    _render_scenario(w, s)
                    w("</details>\n")
                    continue
                _render_scenario(w, s)
            w("")

    w("## Cost breakdown\n")
    w(f"Prices: USD per 1M tokens, standard paid tier, as of {cost['pricing_date']}. "
      "Thinking tokens are billed as output.\n")
    w("| Phase | Calls | Input tokens | Cached | Output tokens | Cost (USD) |")
    w("|---|---|---|---|---|---|")
    for phase, b in sorted(cost["by_phase"].items(), key=lambda kv: -kv[1]["cost_usd"]):
        w(f"| {phase} | {int(b['calls'])} | {int(b['input_tokens']):,} | {int(b['cached_input_tokens']):,} | "
          f"{int(b['output_tokens']):,} | ${b['cost_usd']:.4f} |")
    w(f"| **Total** | **{cost['total_calls']}** | **{cost['total_input_tokens']:,}** | "
      f"**{cost['total_cached_input_tokens']:,}** | **{cost['total_output_tokens']:,}** | **${cost['total_cost_usd']:.4f}** |\n")
    w("| Model | Calls | Cost (USD) |")
    w("|---|---|---|")
    for model, b in cost["by_model"].items():
        w(f"| {model} | {int(b['calls'])} | ${b['cost_usd']:.4f} |")
    per_req = sorted(((r["id"], r.get("cost_usd", 0)) for r in results), key=lambda x: -x[1])
    if results:
        avg = sum(c for _, c in per_req) / len(results)
        w(f"\nAverage cost per requirement: ${avg:.4f}; most expensive: "
          + ", ".join(f"{i} (${c:.4f})" for i, c in per_req[:3]) + ".\n")

    w("## Coverage, gaps and limitations\n")
    deep = [r["id"] for r in results if r.get("depth") == "deep"]
    light = [r["id"] for r in results if r.get("depth") == "light"]
    w(f"- **Deep coverage** (normal, negative and boundary scenarios with side-effect checks): {', '.join(deep)}")
    w(f"- **Light coverage** (main normal path only): {', '.join(light)}")
    interp = context.get("interpret_eval", {})
    if interp:
        rec = interp.get("recall", {})
        w(f"- **Requirement extraction:** {interp.get('validation', {}).get('kept')} requirements extracted; "
          f"coverage of a hand-made PRD inventory {rec.get('covered')}/{rec.get('inventory_size')}"
          + (f" (missed: {', '.join(rec.get('missed', []))})" if rec.get("missed") else ""))
    if story:
        lines.extend(f"- {item}" for item in story.limitations)
        if story.recommendations:
            w("\n### Recommendations\n")
            lines.extend(f"- {item}" for item in story.recommendations)
    if removed:
        w(f"\n<sub>{len(removed)} narrative sentence(s) were removed by the fact check because they cited "
          "numbers not present in the results.</sub>")
    w("\n---\nMethod: [ARCHITECTURE.md](../../docs/ARCHITECTURE.md) · Decisions: [DECISIONS.md](../../docs/DECISIONS.md) "
      "· Raw evidence: `results.json`, `llm_calls.jsonl`, `screenshots/` in this run folder.")
    return "\n".join(lines) + "\n"


def _render_scenario(w, s: dict[str, Any]) -> None:
    status_icon = {"PASS": "✅", "FAIL": "❌", "UNVERIFIED": "⚠️"}[s["status"]]
    w(f"#### {status_icon} Scenario: {s['scenario']}\n")
    w(f"*{s['scenario_type']} · {s['technique']}* · {s['reason']}\n")
    attempt = _final_attempt(s)
    runs = attempt.get("runs") or []
    shown = next((r for r in runs if r["outcome"] == "FAIL"), runs[0] if runs else None)
    if attempt.get("validation_problems"):
        w("Plan validation problems: " + "; ".join(attempt["validation_problems"][:5]) + "\n")
    if shown is None:
        return
    if shown.get("param") is not None:
        failing = [r["param"] for r in runs if r["outcome"] == "FAIL"]
        w(f"Inputs tried (code-generated): {[r['param'] for r in runs]}. Failing inputs: {failing}. "
          f"Steps below are for input {shown['param']!r}.\n")
    w("**Steps to reproduce**\n")
    for st in shown["steps"]:
        if st.get("recovery") is not None and not st.get("ok"):
            continue
        w(f"{st['n']}. {_describe_step(st)}")
    w("")
    if shown["checks"]:
        w("| Expected (check) | Kind | Result | Observed |")
        w("|---|---|---|---|")
        for c in shown["checks"]:
            observed = json.dumps(c["observed"], default=str)[:220].replace("|", "\\|")
            res = {"pass": "✅ pass", "fail": "❌ fail", "error": "⚠️ error"}[c["status"]]
            detail = f" {c['detail']}" if c.get("detail") else ""
            w(f"| {c['description'].replace('|', '/')} | {c['kind']} | {res}{detail[:160]} | `{observed}` |")
        w("")
    review = attempt.get("review")
    if review:
        w(f"Review (majority of {len(review['votes'])}): {review['tally']}  ")
    shots = shown.get("screenshots", [])
    if shots:
        w("Evidence: " + " · ".join(f"[{p.split('/')[-1]}](./{p})" for p in shots[-4:]))
        w(f"\n![final state](./{shots[-1]})\n")
    net = [n for st in shown["steps"] for n in st.get("network", []) if not n.startswith("GET /health")]
    if net:
        w("Network calls: `" + "`, `".join(dict.fromkeys(net)) + "`\n")
