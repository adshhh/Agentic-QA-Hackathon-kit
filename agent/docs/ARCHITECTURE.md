# Architecture

How the agent turns a written PRD into evidence-backed PASS / FAIL / UNVERIFIED verdicts.
The reasoning behind each choice is in [DECISIONS.md](DECISIONS.md) (referenced as D1–D14).

> Status: implemented (`qa_agent/`). Module per phase: interpret.py · explore.py · planner.py
> (plan, critique, revise, validate) · executor.py (execute, recover) · verify.py (verify, judge,
> review) · orchestrator.py (the loop below) · report.py.

**Legend:** 🤖 = model call (Gemini) · ⚙️ = plain code (deterministic)

## The core idea

The model decides *what to test and what "correct" means*. Code does the clicking, the
measuring and the grading. A model is only called again when something goes wrong (D6).

| Phase | Who | Job |
|---|---|---|
| Interpret | 🤖 once | PRD → list of assertions, each quoting the PRD sentence it comes from |
| Explore | ⚙️ crawl + 🤖 summary, once | Live app + `/openapi.json` → map of pages, controls, values and API calls; the repo fills gaps only (D7) |
| Plan | 🤖 per assertion | A JSON-schema test plan built only from steps in the app map, using classic test-design methods, then **self-critiqued** against the PRD sentence (D16) |
| Validate | ⚙️ | Every page and control in the plan must exist in the map |
| Execute | ⚙️ | Run each step; capture screenshots, values and network calls |
| Recover | 🤖 small model, ≤2 retries | ReAct-style fix of a single failing step from a compact page snapshot (D10, D16) |
| Verify | ⚙️ | Apply the plan's checks, plus metamorphic relations and invariants, to the before/after values (D16) |
| Review | ⚙️ every result, 🤖 FAILs only | Rule out glitches; confirm the test expected what the PRD says, by majority of 3 (D8, D16) |
| Revise | 🤖 ≤2 per assertion | Rewrite a plan that couldn't settle its assertion (D9) |
| Report | 🤖 writes, ⚙️ supplies facts | Findings, evidence, gaps and cost (D11) |

## Whole run

```
 INPUTS: PRD.md · target URL · (optional) repo path
            │
            ▼
 ┌─────────────────────────┐
 │ 1. INTERPRET  🤖 once   │  PRD → list of assertions, each with the PRD quote
 └───────────┬─────────────┘  → requirements.json
             ▼
 ┌─────────────────────────┐
 │ 2. EXPLORE  ⚙️ + 🤖 once │  crawl the live app + /openapi.json → map of pages,
 └───────────┬─────────────┘  buttons, fields, values; repo fills gaps → app_map.json
             ▼
 ┌─────────────────────────┐
 │ 3. FOR EACH ASSERTION   │  ──► per-assertion loop (next chart)
 └───────────┬─────────────┘  → results.json (verdict + evidence per assertion)
             ▼
 ┌─────────────────────────┐
 │ 4. REPORT  🤖 + ⚙️ once  │  🤖 writes the explanations; ⚙️ inserts every number,
 └─────────────────────────┘  screenshot and the cost table → report.md, cost.json
```

Every run writes its artifacts to `runs/<timestamp>/`: `requirements.json`, `app_map.json`,
`results.json`, `report.md`, `cost.json`, `screenshots/`.

## Per-assertion loop

Example assertion: *"A transfer above the balance is rejected, and both balances stay the same."*

```
                 ┌──────────────────────────────────────────────┐
                 ▼                                              │
 ┌─────────────────────────────┐                                │
 │ PLAN 🤖 (+ self-critique)    │ writes the test from the app map:
 │ setup · read BEFORE values · │ e.g. read both balances → enter balance+0.01
 │ actions · read AFTER values ·│ → click Transfer → read alert + both balances
 │ expected checks              │ expect: "not completed" shown, balances equal
 └──────────────┬──────────────┘                                │
                ▼                                               │
 ┌─────────────────────────────┐   bad label/page               │
 │ VALIDATE ⚙️                  ├──────────────────► REVISE ─────┤
 │ does every page/button in   │                                │
 │ the plan exist in the map?  │                                │
 └──────────────┬──────────────┘                                │
                ▼ ok                                            │
 ┌─────────────────────────────┐                                │
 │ SETUP ⚙️                     │ local: reset DB + Enter Demo   │
 │                             │ hosted: just read fresh values │
 └──────────────┬──────────────┘                                │
                ▼                                               │
 ┌─────────────────────────────┐  step fails  ┌──────────────┐  │
 │ EXECUTE ⚙️  (step by step)   ├─────────────►│ RECOVER 🤖    │  │
 │ screenshot + values +       │◄─────────────┤ Flash-Lite   │  │
 │ network log at every step   │  retry (≤2)  │ fixes 1 step │  │
 └──────────────┬──────────────┘              └──────┬───────┘  │
                │                       still fails  │          │
                ▼                                    ▼          │
 ┌─────────────────────────────┐            "inconclusive"      │
 │ VERIFY ⚙️                    │                    │          │
 │ compare BEFORE/AFTER values │◄───────────────────┘          │
 │ against expected checks     │                                │
 └──┬───────────┬───────────┬──┘                                │
    │           │           │                                   │
  PASS     inconclusive   a check failed                        │
    │           │           ▼                                   │
    │           │   ┌───────────────────────────┐               │
    │           │   │ REVIEW part 1 ⚙️           │ flaky/agent   │
    │           │   │ re-run once · evidence    ├──► UNVERIFIED │
    │           │   │ complete? agent struggled?│               │
    │           │   └────────────┬──────────────┘               │
    │           │                ▼ still FAIL                   │
    │           │   ┌───────────────────────────┐ test was wrong│
    │           │   │ REVIEW part 2 🤖           ├───────────────┤
    │           │   │ "did the test expect what │               │
    │           │   │  the PRD actually says?"  │               │
    │           │   └────────────┬──────────────┘               │
    │           │                ▼ yes                          │
    │           │              FAIL (with evidence)             │
    │           └──► REVISE 🤖 (new plan, max 2) ───────────────┘
    │                 after 2 revisions ──► UNVERIFIED (with reason)
    ▼
 Record: requirement · PRD quote · plan · step log · before/after values ·
         screenshots · network calls · verdict + reason · tokens & cost
```

### Model calls per assertion

| Situation | Calls |
|---|---|
| Normal case | 2 (PLAN + self-critique) |
| A step fails | + up to 2 (RECOVER, small model) |
| Plan can't settle the assertion | + up to 2 (REVISE, each with its own self-critique) |
| Verdict is FAIL | + 3 (REVIEW part 2, majority of 3) |

## Techniques used (D16)

| Technique | Phase | Role |
|---|---|---|
| Metamorphic testing | Verify | Correct expected answers without knowing seed values (filter subsets, clear-filters restores the count, transfer + reverse restores balances) |
| Invariants + cross-source checks | Verify | Money conserved; balance = opening + completed deltas; UI value = API value |
| Property-based input sweep | Plan / Execute | Code-generated amount edge cases (zero, negative, over-precision, exact balance, balance + 0.01, huge, whitespace, non-numeric) |
| Structured outputs + few-shot | Plan | JSON-schema plans guided by cached example plans |
| Self-critique | Plan | The model checks its own expectations against the PRD sentence before execution |
| Self-consistency | Review | FAIL review asked 3 times, majority wins |
| ReAct recovery + compact snapshot | Recover | Observe the page's controls and labels → reason → one corrective action |
| Classic test design | Plan | Boundary values, equivalence classes, state transitions, decision tables |
| Prompt caching | All plan/critique/revise calls | The step language, test-design guidance and full app map form a stable, cached prompt prefix (D22) |
| Fault injection | Evaluation (outside the run) | Plant known bugs in a local copy and report the agent's catch rate |

### Why each verdict can be trusted

- **PASS:** every expected check held against values read *after* the action, not just a
  successful click.
- **FAIL:** the check failed, it failed again on a re-run, the evidence is complete, the agent
  operated the page without trouble, and the model confirmed the test matches the PRD sentence.
- **UNVERIFIED:** the agent explains why it could not reach a reliable answer (for example,
  it couldn't operate the page, or two rewritten plans were still inconclusive). It never
  claims a PASS it didn't observe.

### Test state

Transfers change balances, so assertions must not interfere with each other. Locally, each
assertion starts from a database reset followed by a fresh Enter Demo (the reset also clears
demo sessions). On the hosted app, where we can't reset, plans compare against values read just
before the action ("source decreased by exactly X") rather than against seed values.
