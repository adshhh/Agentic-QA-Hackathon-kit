# Decision Log

The planning decisions behind the DemoBank QA agent, in the order we made them.

**How to read this log.** Each entry gives the decision, the alternatives we considered, why we
chose it, and the trade-off: what we deliberately gave up. Entries are never edited after the
fact. When a decision changes, a new entry supersedes the old one and says so. The architecture
these decisions produce is drawn in [ARCHITECTURE.md](ARCHITECTURE.md).

---

## D1 · Develop against a local copy; final run against the hosted app

- **Decision:** Run DemoBank locally (PostgreSQL, FastAPI backend, React frontend) during
  development, with a one-command reset to the seeded state (`agent/scripts/reset_db.sh`). Do the
  final recorded run against the organizers' hosted URL once it is available.
- **Alternatives:** Develop directly against the hosted app; or run the kit's Docker setup.
- **Why:** We had no hosted URL yet. Transfers change balances, so repeatable development needs a
  baseline we can restore between runs. Docker wasn't installed, but a local PostgreSQL was.
- **Trade-off:** PostgreSQL 14 instead of the kit's 16 (the schema uses nothing version-specific).
  The hosted build may contain different defects than our local copy.

## D2 · The PRD is truth, the running app is evidence, the source is only context

- **Decision:** The agent may read the target's source to learn routes, labels and API shapes,
  but every verdict must rest on behavior observed in the running app. Nothing is hardcoded to
  look for a specific bug.
- **Alternatives:** Ignore the source entirely; or use it to locate and target suspected defects.
- **Why:** The orientation deck lists the repository as an optional input but says to treat it as
  "context rather than an answer key". The organizers' defect list isn't in the repo, and the
  hosted build may differ, so a bug found only by reading code could be a false positive.
- **Trade-off:** We give up the shortcut of testing straight for defects we spotted in the
  code. The agent must find them on its own.

## D3 · Lightweight development guardrails

- **Decision:** Use a slim set of local development guardrails: automatic lint and unit tests
  after every edit, no automated git operations (the owner commits), and an independent "checker"
  review of claims. These stay out of the submission repo.
- **Alternatives:** A full milestone process (branch per milestone, formal decision records,
  visual review), or no process at all.
- **Why:** It keeps quality checks automatic, and the deadline doesn't allow the full process.
- **Trade-off:** Less formal tracking than the full process.

## D4 · Separate Python environments for the target and the agent

- **Decision:** Two virtual environments, one for the DemoBank backend and one for the agent,
  each auto-activated with direnv.
- **Alternatives:** One shared environment.
- **Why:** The kit asks to keep the target application and the QA agent logically separate.
- **Trade-off:** Slightly more setup.

## D5 · Problem framing: the deck's QA loop, plus seven challenges

- **Decision:** Keep the orientation deck's loop as-is (Interpret → Plan → Execute → Verify →
  Report) and treat the hard parts as seven explicit challenges:
  1. Turn PRD sentences into **checkable** assertions, including hidden side effects ("no
     transaction created", "visible in **both** histories").
  2. Choose normal, negative and boundary scenarios: selection over volume.
  3. Execute autonomously **and** cheaply.
  4. **Verify:** decide PASS/FAIL from before/after state, never from a successful click.
  5. **Test state:** transfers mutate data, so scenarios must not interfere with each other.
  6. Report with evidence captured *during* execution, and cost logged from the first call.
  7. Deliverables (video, README, cost breakdown) before the deadline.
- **Why:** Accuracy and verification rigor are 55% of the score, and the deck stresses that
  "a successful click does not prove that a requirement passed".

## D6 · Hybrid execution: the model writes the test, code runs and grades it

- **Decision:** For each assertion, the model writes a complete step-by-step test plan (setup,
  values to read before, actions, values to read after, and the expected checks), built only from
  steps the agent has discovered in the app. Plain code executes the plan, captures evidence,
  logs every state change and evaluates the checks.
- **Alternatives:**
  - The model drives the browser click by click: the most autonomous, but costly, slower,
    and prone to false positives.
  - Hand-written DemoBank-specific scripts with the model used only for planning: the
    fastest to build, but brittle, and not really an agent.
- **Why:** The model's judgment goes where it matters (what to test and what "correct"
  means), and code does the repetitive, exact work. That keeps runs cheap and repeatable while
  the agent still decides what to test.
- **Trade-off:** Less moment-to-moment improvisation than a fully model-driven browser agent.

## D7 · Learn the app from the live site first; the repo only fills gaps

- **Decision:** The agent maps the app by crawling the running site and reading its API
  description (`/openapi.json`). It uses the repository only to fill gaps the crawl leaves (for
  example, error messages that appear only in certain states). The map records where each entry
  came from.
- **Alternatives:** Live site only; or build the map from the source first.
- **Why:** It treats the app as a black box first, as a real tester would, without getting
  stuck when something can't be discovered from the outside.
- **Trade-off:** A slightly slower start than reading everything from the source.

## D8 · Two-part verdict review before any FAIL is reported

- **Decision:**
  - **Part 1, code, every result:** re-run once to rule out timing glitches, confirm the
    evidence is complete, and downgrade to UNVERIFIED if the agent itself struggled to operate
    the page.
  - **Part 2, model, FAILs only:** re-read the PRD sentence and ask, *"did this test expect what
    the PRD actually says?"* If not, the test is rewritten (see D9).
- **Alternatives:** Code-only review; or a model review of every result.
- **Why:** When a test fails, either the app is broken or our test is. Code can catch a broken
  *run* (page didn't load, button not found), but not a test that *expected the wrong thing*,
  since both look identical: "expected X, got Y". Example: a test that reads dates in UTC when the
  app (correctly) uses Pacific Time would wrongly flag the date filter. Only re-reading the PRD
  catches that.
- **Trade-off:** We don't model-review PASSes. A wrongly passed test hurts less than a wrongly
  reported bug, and it saves about 25 model calls per run.

## D9 · Rewrite a test plan only when it couldn't settle its assertion, at most twice

- **Decision:** If a plan ends without a conclusive answer (an execution problem, a missing
  precondition, an ambiguous observation, or a test the review found to be wrong), the model
  writes a new plan. At most **2 rewrites** per assertion; after that the assertion is marked
  UNVERIFIED with the reason, and the agent moves on.
- **Alternatives:** Unlimited retries; or no rewrites at all.
- **Why:** The deck asks for an agent that can "revise its plan, retry safely and explain what
  it could not verify", and a hard cap keeps cost and runtime bounded.
- **Trade-off:** A few assertions may end UNVERIFIED that more attempts could have settled.

## D10 · Fix a single failing step, at most twice

- **Decision:** When one step can't find its target or the page is in an unexpected state, a
  small, cheap model looks at a compact snapshot of the page and proposes a fix for that step
  only. At most **2 retries**; then the run counts as inconclusive (which can trigger D9).
- **Why:** It handles slow-loading pages and small UI differences without rewriting the whole
  test. Resilience is 15% of the score.
- **Trade-off:** Bounded retries mean a genuinely broken page is reported rather than retried
  forever.

## D11 · The model writes the report; code supplies every fact

- **Decision:** Code collects all results (verdicts, observed values, screenshot paths, token
  counts) into `results.json`. The model writes the explanations, findings, limitations and gaps
  from that file only. Code inserts every number, table, screenshot and the cost table, then
  checks that every value the text cites exists in the results.
- **Alternatives:** A purely code-generated report; or a free-form model-written report.
- **Why:** The model writes clearer explanations for a developer who hasn't seen the run,
  but a QA report that invents a balance would be worse than no report.
- **Trade-off:** One extra model call per run (included in the cost table).

## D12 · Python + Playwright

- **Decision:** The agent is written in Python and drives the browser with Playwright.
- **Alternatives:** TypeScript + Playwright.
- **Why:** The Python environment was already set up, both libraries are mature, and Python is
  convenient for JSON handling and report generation.

## D13 · Gemini models, split by role

- **Decision:** `gemini-3.8-flash` for every thinking role (interpret, app-map summary, plan,
  rewrite, review, report) and `gemini-3.5-flash-lite` for single-step fixes. All calls go
  through one thin wrapper that logs tokens and cost per model and per phase, so the provider can
  be swapped through config.
- **Alternatives:**
  - Gemini's heavier `gemini-4-argon`: judged unnecessary for this task.
  - Paid Claude API (Sonnet + Haiku).
  - A local open model via Ollama: free, but 8 GB of RAM shared with the browser and the app
    is too tight, and small models raise the false-positive risk.
  - Hosted open models.
- **Why:** Prepaid Gemini credit was available, the kit places no restriction on models (only
  that cost is reported), and the estimated cost is about **$0.23 per full run**. Prices are
  standard paid-tier list prices fetched 2026-10-21: `gemini-3.8-flash` $0.75 in / $3.75 out per
  million tokens (scheduled to double on 2027-01-01); `gemini-3.5-flash-lite` $0.30 / $2.50.
- **Validation instead of assumption:** we have no prior benchmark for these models on this
  task, so we measure. A small eval runs three assertions of increasing difficulty and checks that
  each plan is valid, each verdict is conclusive and matches a hand-checked answer, and what each
  one cost:
  - **Easy:** Enter Demo opens the dashboard without credentials.
  - **Medium:** every displayed row satisfies all selected filters.
  - **Hard:** an over-balance transfer is rejected, and both balances and the history are
    unchanged.

  We also score the extracted requirements against a hand-made inventory of the PRD.
- **Trade-off:** A cheaper model may plan less well than a heavyweight one. The eval exists to
  catch that early.

## D14 · This log is public

- **Decision:** Keep this log at `agent/docs/DECISIONS.md` in the submission repository.
- **Why:** It explains the architecture's reasoning to judges and supports the walkthrough video.

## D15 · Gemini stays the default; open models are a config switch away

- **Decision:** Keep Gemini (D13) as the default. The model wrapper also accepts any
  OpenAI-compatible endpoint (e.g. a local Ollama server or OpenRouter), so an open model can be
  swapped in through configuration without code changes. Reasoning techniques (chain-of-thought,
  plan-and-solve, self-consistency) are applied regardless of which model runs.
- **Alternatives:** Switch to a local open model (≈7–8B on an 8 GB machine); a hosted open
  model; Gemini only, with no open-model path.
- **Why:** Prompting techniques are independent of the model. Running a local model on 8 GB of
  RAM alongside the browser and the app would be slow and less reliable at long structured
  plans, which risks the 55% of the score tied to accuracy and rigor. Keeping the wrapper
  open-compatible preserves the option at almost no cost.
- **Trade-off:** API cost stays slightly above zero (≈$0.23 per run) instead of $0.

## D16 · Techniques adopted beyond plain prompting

- **Decision:** Ten techniques, grouped by where they act:
  - **Verification (how PASS/FAIL is decided)**
    1. **Metamorphic testing:** check relationships instead of exact values. The rows for
       "deposit AND completed" must be a subset of "deposit". Clearing filters must restore the
       original row count. A transfer followed by the same transfer reversed must restore both
       balances.
    2. **Invariants and cross-source checks:** internal transfers conserve total money; each
       balance equals the opening balance plus its completed transaction deltas; the value on
       screen equals the API value.
    3. **Fault injection:** plant known bugs in a temporary *local* copy of DemoBank (for
       example, a flipped transfer sign), run the agent, and report how many it caught. The
       target kit is always restored afterwards and never committed modified.
    4. **Property-based input sweep:** code, not the model, generates amount edge cases (zero,
       negative, over-precision, exact balance, balance + 0.01, very large, whitespace,
       non-numeric).
  - **Model calls**
    5. **Structured outputs + few-shot examples:** plans must match a JSON schema, guided by 2–3
       cached example plans.
    6. **Self-critique before running:** the model checks its own expected checks against the
       PRD sentence before any execution.
    7. **Self-consistency on FAIL review:** the review question is asked 3 times and the majority
       wins.
    8. **ReAct-style recovery with a compact page snapshot:** step fixes see the page's list of
       controls and labels (its accessibility tree), not raw HTML.
  - **Test design and cost**
    9. **Classic test design** in the plan prompt: boundary-value analysis, equivalence
       partitioning, state-transition testing and decision tables.
    10. **Prompt caching + map slices:** the PRD and app map are cached, and each plan call
        receives only the pages it needs.
- **Alternatives considered and skipped:** fine-tuning and automated prompt optimizers (no
  time); tree-of-thought (cost out of proportion); batch API discounts (too slow for a live demo);
  plan reuse across runs and a screenshot judge for subjective requirements (stretch goals, not
  committed).
- **Why:** Most of the score comes from accuracy and rigor. Techniques 1–4 give the agent
  trustworthy answers without knowing seed values, and fault injection *measures* how many real
  bugs the agent catches instead of assuming it. Techniques 5–8 make model output reliable and
  self-checked; 9–10 make coverage systematic and calls cheap.
- **Trade-off:** Self-critique (+1 call per assertion) and self-consistency (+2 calls per FAIL)
  raise the estimated run cost from about $0.23 to roughly $0.30–0.40 (to be measured).
  Fault injection costs build time and extra runs.

## D17 · Test depth: three areas deep, the rest light

- **Decision:** **Deep coverage** (normal, negative and boundary scenarios, with side effects
  checked) for **Users and access**, **Transfers between owned accounts**, and **Transaction
  history**. **Light coverage** (main normal path plus obvious checks) for Dashboard and
  accounts, Profile, General interface behavior, and Seed data.
- **Alternatives:** Equal depth everywhere.
- **Why:** These three areas hold most of the PRD's verifiable behavior and the highest-risk
  logic (money movement, filtering, access control). Time is limited, so depth goes where defects
  matter most.
- **Trade-off:** Lightly tested areas may hide defects a deeper pass would find. The report
  states the depth applied to each requirement.

## D18 · Report format: Markdown

- **Decision:** The audit report is Markdown (`report.md`) with linked screenshots.
- **Alternatives:** HTML or PDF.
- **Why:** Fastest to produce and readable on GitHub. Can be converted later if needed.
- **Trade-off:** Less visual polish than an HTML report.

## D19 · Subjective wording: code proves the facts, the model reads the text

- **Decision:** For opinion-based wording ("clearly labeled", "clear explanation", "clear
  validation message", "clear empty state", "amount direction should be clear", "readable labels",
  "usable at narrower widths"), each requirement is split in two:
  - **Facts, checked by code:** e.g. an error was shown, no success message appeared, balances
    and history were unchanged.
  - **Clarity, judged by the model from text only:** the model reads the observed message and
    answers one narrow question, e.g. *"Does this message say why the transfer was rejected?"*.
    The report quotes the exact text so a developer can disagree.
  - **Visual-only wording** gets a code proxy where one exists (e.g. no horizontal scrolling at
    a 375px-wide viewport); otherwise it is UNVERIFIED with the reason.
- **Alternatives:** Code facts only (clarity always UNVERIFIED); a screenshot-based judge.
- **Why:** It gives an evidence-backed answer for about 6 requirements at the cost of a few
  cheap text-only calls, without claiming more than the evidence shows.
- **Trade-off:** The clarity judgement is still a model's opinion. That's mitigated by quoting
  the text and keeping the question narrow. Visual polish isn't judged.

---

## Build-time decisions (made during implementation, logged for review)

## D20 · Plans and checks use a small step language and a sandboxed expression language

- **Decision:** The model writes plans as JSON steps from a fixed vocabulary (`start_session`,
  `goto`, `click`, `fill`, `select`, `read_text`, `read_table`, `read_api`, …) and checks as
  boolean expressions (e.g. `money(src_after) == money(src_before)`). Code evaluates the checks
  in a sandbox: the syntax tree is whitelisted, only listed helper functions may be called, and
  attribute access, imports, lambdas and dunder names are rejected. Step values may contain
  `{{expressions}}`, e.g. `{{money(balance) + dec('0.01')}}` for a runtime boundary value.
- **Alternatives:** Letting the model generate Playwright code (powerful, but unsafe and hard to
  validate); hard-coded check types (safe, but too rigid for metamorphic relations and invariants).
- **Why:** It keeps model-written tests both *expressive* and *checkable before running*: a plan that
  references a control or API path missing from the app map is rejected and revised.
- **Trade-off:** The model must learn the language from the prompt, and some valid ideas don't fit it.

## D21 · Few-shot examples come from a fictional app, not DemoBank

- **Decision:** The planner's worked examples use an invented shop app (cart quantities, order
  filters), never DemoBank.
- **Why:** Examples written for DemoBank would hand the agent answers about the very app it is
  judging. Generic examples teach the format and the rigor without biasing verdicts.

## D22 · Full app map in a cached prompt instead of per-plan slices

- **Decision:** Every plan call receives the whole (compact) app map in a stable system prompt,
  rather than only the pages relevant to that requirement. *Supersedes the "map slices" part of
  D16 #10.*
- **Why:** Gemini's prompt caching only works on an identical prefix. Per-requirement slices would
  change the prefix on every call and defeat the cache. The full map is small (about 3K tokens) and
  is billed at the cached rate after the first call.
- **Trade-off:** A little more context per call than strictly needed.

## D23 · Near-verbatim PRD quotes are repaired, not trusted

- **Decision:** A requirement's PRD quote must appear verbatim (sentence by sentence). If the model
  paraphrased slightly (≥85% similar to a real PRD sentence), the quote is replaced with the real
  sentence and flagged. Anything else is dropped and logged.
- **Why:** The first extraction dropped two real requirements over trivial wording differences
  (recall 31/36); after repair, recall was 36/36 with no invented requirements accepted.

## D24 · Hardening found by the eval

- **Decision:**
  - Per-phase output-token caps, with one retry on unparsable output.
  - Failed and retried model requests are billed in the cost ledger.
  - Explore records every API endpoint's response *shape* from OpenAPI, and the planner is told
    to use it exactly.
  - Table headers are read without CSS text transforms.
- **Why:** The eval exposed a runaway 65K-token generation (which had been logged as $0) and two
  checks that passed vacuously because they assumed the wrong JSON shape and the wrong header case.
  Details: [eval/EVAL_RESULTS.md](../eval/EVAL_RESULTS.md).

## D25 · Fault injection runs on a separate, temporary stack

- **Decision:** Each planted bug is applied to a temporary *copy* of the backend, served on port
  8001 with a second frontend on 5174. The kit source and the normal servers are never modified.
  Faults are placed in behaviors the baseline run found correct, so a detection is attributable to
  the planted bug.
- **Trade-off:** It only covers backend faults. Frontend-only faults would need a patched frontend copy.

---

## Still open

- Video plan (owner's call).
- Hosted target URL (awaiting the organizers).
