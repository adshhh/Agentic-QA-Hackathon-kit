# DemoBank QA Agent

An autonomous, specification-driven QA agent. It reads a written PRD, learns a live web app by
crawling it, writes and executes its own test plans in a real browser, checks side effects
(balances, records, redirects, API state), and reports every requirement as **PASS**, **FAIL** or
**UNVERIFIED** with reproducible evidence and a token-level cost breakdown.

Built for the CDF Agentic QA Hackathon. The target is DemoBank (`../test-website`).

- **Architecture and the per-requirement loop:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Every design decision, with alternatives and trade-offs:** [docs/DECISIONS.md](docs/DECISIONS.md)
- **Model validation eval (3/3 verdicts match hand-checked ground truth):** [eval/EVAL_RESULTS.md](eval/EVAL_RESULTS.md)
- **Fault-injection results:** [eval/FAULT_INJECTION.md](eval/FAULT_INJECTION.md)
- **Sample audit report from a full run:** see [Results](#results)

## How it works (one paragraph)

The model decides *what to test and what "correct" means*; plain code does the clicking, measuring
and grading. **Interpret:** one model call turns the PRD into atomic requirements, each quoting the
PRD verbatim (code rejects invented quotes). **Explore:** code crawls the live app without changing
data and reads its OpenAPI description; the source repo, if given, only fills gaps. **Plan:** per
requirement, the model writes executable test plans in a small step language, choosing normal,
negative and boundary scenarios (boundary-value analysis, equivalence partitioning, state
transitions, metamorphic relations, invariants), then critiques its own plan against the PRD.
**Validate:** code rejects plans that reference controls or API paths the app doesn't have.
**Execute:** Playwright runs the steps in a fresh browser, with a database reset when testing
locally, capturing screenshots, values, network calls and console errors. A cheap model fixes a
single failing step (≤2 retries). **Verify:** code evaluates the plan's checks in a sandboxed
expression language. **Review:** a FAIL must reproduce on a fresh re-run, and a majority of 3
model votes must agree the expectation matches the PRD sentence. Otherwise the plan is revised
(≤2 times) or the requirement is UNVERIFIED. **Report:** the model writes the narrative; code
inserts every number, step, value and screenshot, and removes sentences citing unsupported numbers.

## Requirements

- macOS or Linux, Python 3.11+, Node 20+ (for the target), PostgreSQL 14+ **or** Docker (for the local target)
- A Gemini API key (default provider), **or** any OpenAI-compatible endpoint (e.g. a local Ollama server or OpenRouter)
- About 1 GB of disk for Chromium

## Setup

### 1. The target (local copy of DemoBank)

Follow `../test-website/LOCAL_SETUP.md` (Docker), or, with an existing PostgreSQL instead of Docker:

```bash
psql -d postgres -c "CREATE ROLE demobank LOGIN PASSWORD 'demobank_local';" \
                 -c "CREATE DATABASE demobank OWNER demobank;"
agent/scripts/reset_db.sh          # applies the kit's migration + seed (also the reset between runs)
# then start the backend (:8000) and frontend (:5173) as in LOCAL_SETUP.md
```

### 2. The agent

```bash
cd agent
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
playwright install chromium
cp .env.example .env               # then set GEMINI_API_KEY (never commit .env)
set -a; source .env; set +a        # or use direnv
qa-agent ping                      # one model call: confirms the key and logs tokens + cost
```

## Run

```bash
# Full audit: PRD + live app (+ optional source for gap-filling) -> runs/<timestamp>/report.md
qa-agent run --prd ../test-website/PRD.md --url http://localhost:5173 --repo ../test-website/frontend/src

# Watch it work (for demos): visible browser, slowed down
qa-agent run --prd ../test-website/PRD.md --only TRF-04,TXN-03 --headed --slow-mo 400

# Against the organizers' hosted app: no database resets; plans compare relative to values read first
qa-agent run --prd ../test-website/PRD.md --url https://<hosted-demobank>

# Individual stages
qa-agent interpret --prd ../test-website/PRD.md        # -> requirements.json + recall vs inventory
qa-agent explore --url http://localhost:5173           # -> app_map.json

# Reuse earlier stages while iterating (saves cost)
qa-agent run --prd ../test-website/PRD.md --requirements runs/<run>/requirements.json --app-map runs/<run>/app_map.json

# Fault injection: plant one known bug in a temporary copy of the backend and see if the agent catches it
python scripts/fault_injection.py --list
python scripts/fault_injection.py --fault same_account_allowed --only <REQ-ID> --requirements runs/<run>/requirements.json
```

Configuration (environment variables, see `.env.example`): `TARGET_URL`, `API_URL`,
`DEMOBANK_DATABASE_URL` (local only; enables resets), `QA_PROVIDER` (`gemini` | `openai_compat`),
`QA_MODEL_THINKING`, `QA_MODEL_LIGHT`, `OPENAI_COMPAT_BASE_URL`, `OPENAI_COMPAT_API_KEY`.

## Output (per run, in `runs/<timestamp>/`)

| File | Contents |
|---|---|
| `report.md` | The audit report: verdict per requirement, reproduction steps, expected vs observed, screenshots, review votes, cost breakdown, coverage and limitations |
| `results.json` | Everything behind the report: plans, every step, saved values, check results, network calls, reviews, revisions |
| `requirements.json` | Extracted requirements with verbatim PRD quotes and test depth |
| `interpret_eval.json` | Quote validation and recall against the hand-made PRD inventory |
| `app_map.json` | Crawled pages, controls, API endpoints and response shapes, repo hints (with provenance) |
| `llm_calls.jsonl` | One line per model request: phase, model, input/cached/output/thinking tokens, cost, latency |
| `cost.json` | Cost totals by phase and model |
| `screenshots/` | JPEG after every state-changing step and at the end of every scenario |

## Results

**Official sample run:** [sample_run/20261021-092147/report.md](sample_run/20261021-092147/report.md)
(local copy of DemoBank, database reset between scenarios, headless).

| | |
|---|---|
| Requirements extracted from the PRD | 32 (coverage of a hand-made PRD inventory: 36/36) |
| Verdicts | **27 PASS · 3 FAIL · 2 UNVERIFIED** |
| Scenario runs executed | normal, negative and boundary scenarios per deep requirement; 10 code-generated invalid amounts per amount-validation plan |
| Runtime | 57 min |
| Model cost | **$2.02** for the whole run (138 model calls; gemini-3.8-flash + gemini-3.5-flash-lite, prices as of 2026-10-21) |

**Defects found (each reproduced on a fresh re-run and confirmed 3-0 by the PRD review):**

| ID | Defect | Evidence |
|---|---|---|
| TRF-03 | Zero and negative amounts are accepted | `0` creates a completed $0.00 transfer; in the first full run, `-5` moved money *backwards* (checking $4,369.75 → $4,374.75) |
| TRF-04 | Overdrafts are accepted | Transferring $4,369.76 from a $4,369.75 balance succeeds with no error; checking shows −$0.01 in the UI and API |
| TXN-04 | Date filter end date is not inclusive | A transaction on the selected end date is missing from the filtered results |

An earlier full run (before the D26 fixes) also flagged **pending and failed rows showing "$0.00, No
change"** instead of their amount (TXN-02). In the official run, the planner wrote different
scenarios and TXN-02 passed. That's a known effect of model nondeterminism (see Known limitations).

**UNVERIFIED, with reasons:**
- **TRF-07** ("if a transfer is rejected, the UI must explain…"): the scenarios it tried were *not
  rejected* (the overdraft and zero-amount bugs). The 3-vote review correctly refused to count those
  bugs again under this conditional requirement, a false positive that was avoided.
- **ACC-01**: harness issue. The plan navigated to the API origin instead of the app. Fixed (D27);
  re-tested in [sample_run/20261021-102012-ACC-01-retest](sample_run/20261021-102012-ACC-01-retest/report.md):
  **PASS** (2 scenarios, $0.05).

**Model validation eval:** 3/3 verdicts match hand-checked ground truth ([eval/EVAL_RESULTS.md](eval/EVAL_RESULTS.md)).
**Fault injection:** 5 bugs planted in a temporary copy of the backend. After one round of fixes
(D28), the agent catches **3 of the 4 that are observable** (same-account transfer allowed, destination
credited 1¢ short, email unmasked) with no new false positives on the healthy app. It still misses
an ignored status filter. Details and honest reading: [eval/FAULT_INJECTION.md](eval/FAULT_INJECTION.md).

## Cost

**Measured:** $2.02 for a full 32-requirement run, i.e. about $0.06 per requirement. Planning and
self-critique account for most of it (see the cost table in the sample report). Single-requirement
re-runs cost $0.05–0.15. The model choice keeps a full audit well under a dollar per ten requirements.

Every model request is logged with its tokens and priced from a dated table (`qa_agent/config.py`;
Gemini standard paid tier as of 2026-10-21; thinking tokens are billed as output; failed and
retried requests are billed too). The report's cost section is computed after the narrative call,
so it includes the cost of writing the report itself.

## Known limitations

- **Model nondeterminism.** Requirement extraction and plans vary between runs (e.g. 32 vs 34
  requirements). Verdicts are stable on clear defects; borderline items can flip between PASS and
  UNVERIFIED. The safeguards (re-run, 3-vote review, PRD-quote validation) bias toward UNVERIFIED,
  not toward false FAILs.
- **Local vs hosted.** Development and the sample run use a local copy with database resets. On the
  hosted app the agent can't reset state, so plans compare against values read first; results on a
  shared instance can be affected by other users' activity.
- **Subjective wording** ("clear", "readable") is judged by the model from the observed *text* only,
  and the report quotes the text. Visual layout is checked by code proxies only (e.g. horizontal
  overflow at narrow widths).
- **Light coverage** for dashboard, profile, general UI and seed-data requirements (one normal-path
  scenario each), by design (DECISIONS.md D17).
- **Filter isolation.** When a PRD sentence lists several filters, the planner may test one of them
  only in combination with others, which can hide a broken filter (the ignored-status-filter fault
  was missed this way; see eval/FAULT_INJECTION.md).
- **The official sample run predates the last prompt fixes (D27, D28).** Those fixes were validated by
  targeted re-runs (ACC-01 re-test, fault-injection round 2, healthy-app checks) rather than a second
  full run.
- **Single customer.** "Data belonging to another user" can only be checked indirectly (no second user
  exists in the target).
- **Exploration is non-destructive**, so states that require submitting forms (e.g. error messages)
  are learned from the repo when one is given; without a repo, plans discover them at run time.
- **Runtime:** a full run takes tens of minutes, because every mutating scenario resets the database
  and starts a fresh browser.

## Repository layout

```
agent/
  qa_agent/
    cli.py            entry point (qa-agent ping | interpret | explore | run)
    llm.py            provider-agnostic model wrapper + cost ledger
    config.py         model per phase, dated pricing table
    interpret.py      PRD -> requirements, verbatim-quote validation
    explore.py        non-destructive crawl, API + OpenAPI discovery, repo gap-filling
    planner.py        plan / self-critique / revise prompts, plan validation against the app map
    expr.py           sandboxed check language and helpers (money, dates in app timezone, ...)
    executor.py       step runner, evidence capture, step recovery
    verify.py         check evaluation, subjective judge, 3-vote failure review
    orchestrator.py   the per-requirement loop and verdict aggregation
    report.py         Markdown report: model narrative + code-inserted facts
    browser.py        Playwright helpers: compact snapshots, network log, settle
  scripts/            reset_db.sh, fault_injection.py, demo.sh (headed demo), publish_run.py
  sample_run/         the official full run (report, results, raw logs, linked screenshots)
  eval/               PRD inventory, eval results, fault-injection results
  docs/               ARCHITECTURE.md, DECISIONS.md
  tests/unit/         unit tests (cost accounting, quote validation, sandbox, dates)
```

## Safety

Synthetic data only. No secrets in the repo (`.env` is git-ignored; `.env.example` is provided).
Exploration never submits forms. The agent only talks to the target URL and its own API origin.
No load testing.
