"""PLAN / CRITIQUE / REVISE (model) and VALIDATE (code).

The model writes test plans in a small step language (schemas.Step) with checks in a sandboxed
expression language (expr.py). Code validates every plan against the explored app map before
anything runs, so a plan that names a control the app doesn't have is rewritten, not executed.
"""

from __future__ import annotations

import json
from typing import Any

from .browser import route_pattern
from .expr import HELPER_DOCS, ExprError, free_variables, template_variables
from .llm import LLM
from .schemas import Critique, PlanSet, Requirement, TestPlan

# Code-generated inputs for parameterized plans (property-based input sweep, DECISIONS.md D16).
PARAMETER_SETS: dict[str, list[str]] = {
    "invalid_amounts": ["", "   ", "0", "0.00", "-5", "-0.01", "abc", "ten", "1.005", "12.345"],
    "valid_amount_boundaries": ["0.01", "1", "10.50", "999.99"],
}

STEP_DOCS = """STEP LANGUAGE (each step is a JSON object):
  action: start_session | goto | click | fill | select | press | reload | back | clear_cookies |
          set_viewport | read_text | read_all_text | read_count | read_value | read_table | read_url |
          read_api | wait_for_text | measure_overflow
  by: testid | role | label | text | placeholder | none      (how to find the element)
  locator: the testid / ARIA role / label text / visible text / placeholder
  name: accessible name (only with by=role; substring match)
  value: goto path, text to fill, option label to select, key to press, API path, "375x812" viewport
  save_as: variable name for read_* results
  optional: true if the element may legitimately be absent (read returns null instead of failing)

Semantics:
  start_session      open the landing page and perform the app's entry action (fresh session)
  goto               value = path like /transfer (relative to the app)
  select             value = visible option label or a unique substring of it
  read_text          visible text of the FIRST match
  read_all_text      list of texts of ALL matches
  read_count         number of matches (0 if none)
  read_value         current value of an input; for selects, the selected option's label
  read_table         rows of a table as a list of {column header: cell text}
  read_url           current path + query, e.g. "/transactions?type=deposit"
  read_api           value = API path, e.g. /api/accounts. GET with the browser's session.
                     Saves {"status": <int>, "body": <parsed JSON or null>}
  wait_for_text      waits until the text is visible (fails if it never appears)
  measure_overflow   saves true if the page scrolls horizontally
Values may contain {{expressions}} evaluated against saved variables, e.g.
  "value": "{{money(src_before) + dec('0.01')}}"
  Inside a parameterized plan, {{param}} is the current code-generated input.
After every action the runner waits for the network to go idle and loading indicators to clear."""

CHECK_DOCS = """CHECKS: each check is a restricted Python boolean expression over saved variables.
""" + HELPER_DOCS + """
Kinds: functional | side_effect | metamorphic | invariant | cross_source | subjective
  side_effect  : something that must (or must NOT) change, e.g. balances unchanged after a rejection
  metamorphic  : a relation between two observations instead of an exact value, e.g. filtered rows
                 are a subset of unfiltered rows; clearing filters restores the original count
  invariant    : must always hold, e.g. total money across own accounts is unchanged by an
                 internal transfer; UI balance equals API balance
  cross_source : the UI agrees with the API
  subjective   : opinion-based wording ("clear", "readable"). The expression only checks that the
                 text exists; also set subjective_question (a narrow yes/no question about the text)
                 and subject_var (the variable holding the text). A model answers the question."""

DESIGN_DOCS = """TEST DESIGN (choose deliberately and name the technique):
  - Equivalence partitioning and boundary-value analysis for inputs (amounts, dates).
  - State-transition testing for sessions (in -> logged out -> refresh / direct navigation).
  - Decision tables for filter combinations.
  - Every requirement with side effects needs BEFORE and AFTER observations of those side effects.
  - Never rely on seed data or hard-coded balances: read values first and compare relative to them.
  - A successful click proves nothing: verify the resulting state (values, records, URL, messages).
  - Prefer stable locators: testid > role+name > label > text.
  - Use read_api for cross-checks and for facts the UI may hide (but judge the requirement on what
    the PRD says the USER sees, unless the PRD speaks about data or the DOM).
  - read_api saves {"status", "body"}; "body" follows the endpoint's body shape in the app map
    EXACTLY (lists are usually nested under a key, e.g. x["body"]["items"]). A check on the wrong
    shape passes vacuously, which is the worst possible bug in a test. Table rows are keyed by the
    column headers as shown in the app map (use get()/col(), which match keys case-insensitively).
  - Use code-generated inputs via parameter_set instead of inventing long input lists:
      invalid_amounts         = """ + json.dumps(PARAMETER_SETS["invalid_amounts"]) + """
      valid_amount_boundaries = """ + json.dumps(PARAMETER_SETS["valid_amount_boundaries"]) + """
  - Exact-balance and balance+0.01 boundaries must be computed at runtime with {{expressions}}.
  - Dates: the UI shows times in the app's timezone; compare dates with ui_date()/api_date().
  - Keep each plan focused: 1 scenario per distinct behavior, at most 14 steps and 6 checks.
    Checks must follow from the PRD quote, not from assumptions about how the app is built.
  - Error/validation messages may appear as an alert, a status region or plain text next to a
    field. When the exact element is unknown, read the text with optional=true from more than one
    place, or check that the expected outcome did NOT happen (no success message, nothing changed).
  - subjective_question must ask ONLY what the PRD wording requires (e.g. "Does this message tell
    the user the transfer did not complete and why?"), never extra criteria the PRD doesn't state.
  - Keep the objective consequences (balances, records) in separate checks from message checks, so
    a weak message check never hides a real side effect.
  - PROPERTY COVERAGE: every property word in the PRD quote ("masked", "two decimal places",
    "inclusive", "only", "every", "exactly one", "never") needs a check that would FAIL if the
    property were absent. E.g. "masked email" -> the visible email must hide most of the local part
    (contain a mask character such as * or • and not equal a plausible full address); "two decimal
    places" -> the amount text matches \\.\\d{2} AND the value equals the API value exactly.
  - DISCRIMINATING DATA: a filter/selection check is only meaningful if the unfiltered data contains
    rows that should be EXCLUDED. Test each filter dimension on its own with a value that excludes
    some rows, assert such rows existed before filtering, and assert they are gone after. Avoid
    combinations where one filter makes another irrelevant (e.g. a type whose rows all share one status).
  - ENUMERATION COVERAGE: when the PRD quote lists several items (e.g. filters, fields, input
    classes), EVERY listed item must be exercised by at least one plan; group items into fewer plans
    if needed, but never drop one."""

FEW_SHOT = """EXAMPLE (for a different, fictional shop app; shows the format and the rigor expected)
Requirement: "Quantity must be a whole number from 1 to 99; invalid quantities are rejected with a
message and the cart total does not change."
{"requirement_id":"CART-02","plans":[
 {"scenario":"Invalid quantity is rejected and the cart is unchanged","scenario_type":"negative",
  "technique":"equivalence partitioning (code-generated invalid inputs)","needs_clean_state":true,
  "parameter_set":"invalid_amounts",
  "steps":[
   {"action":"start_session"},
   {"action":"goto","value":"/cart"},
   {"action":"read_text","by":"testid","locator":"cart-total","save_as":"total_before"},
   {"action":"read_api","value":"/api/cart","save_as":"api_before"},
   {"action":"fill","by":"label","locator":"Quantity","value":"{{param}}"},
   {"action":"click","by":"role","locator":"button","name":"Update"},
   {"action":"read_text","by":"role","locator":"alert","save_as":"error_text","optional":true},
   {"action":"read_text","by":"testid","locator":"cart-total","save_as":"total_after"},
   {"action":"read_api","value":"/api/cart","save_as":"api_after"}],
  "checks":[
   {"description":"An error message is shown","kind":"functional","expression":"error_text is not None and len(strip(error_text)) > 0"},
   {"description":"Cart total unchanged (PRD: total does not change)","kind":"side_effect","expression":"money(total_after) == money(total_before)"},
   {"description":"No item changed server-side","kind":"side_effect","expression":"api_after[\\"body\\"] == api_before[\\"body\\"]"},
   {"description":"Message explains the problem","kind":"subjective","expression":"error_text is not None","subjective_question":"Does this message tell the user why the quantity was rejected?","subject_var":"error_text"}]},
 {"scenario":"Filtering orders by status keeps only matching rows","scenario_type":"normal",
  "technique":"decision table + metamorphic relation","needs_clean_state":false,"parameter_set":"none",
  "steps":[
   {"action":"start_session"},{"action":"goto","value":"/orders"},
   {"action":"read_table","by":"testid","locator":"orders-table","save_as":"all_rows"},
   {"action":"select","by":"label","locator":"Status","value":"Shipped"},
   {"action":"click","by":"role","locator":"button","name":"Apply"},
   {"action":"read_table","by":"testid","locator":"orders-table","save_as":"shipped_rows","optional":true}],
  "checks":[
   {"description":"Every displayed row has status Shipped","kind":"functional","expression":"all(lower(s) == \\"shipped\\" for s in col(shipped_rows or [], \\"Status\\"))"},
   {"description":"Filtered rows are a subset of all rows","kind":"metamorphic","expression":"len(shipped_rows or []) <= len(all_rows)"}]}]}"""


def _compact_map(app_map: dict[str, Any]) -> str:
    pages = []
    for pg in app_map["pages"].values():
        els = []
        for el in pg["elements"]:
            item = {k: el[k] for k in ("role", "name", "testid", "type", "count") if el.get(k) not in (None, "")}
            if el.get("options"):
                item["options"] = [o["label"] for o in el["options"]]
            els.append(item)
        pages.append({"route": pg["route"], "elements": els})
    api = [
        {"endpoint": f"{e['method']} {e['path']}", "params": e["params"], "status_codes": e["responses"],
         "body": e.get("response_shape"), **({"request": e["request_shape"]} if e.get("request_shape") else {})}
        for e in app_map["api"]["endpoints"]
    ]
    hints = [h["value"] for h in app_map["repo_hints"]["hints"]]
    return json.dumps(
        {
            "entry": app_map["summary"]["entry_action"],
            "pages": pages,
            "page_purposes": {p["route"]: p["purpose"] for p in app_map["summary"]["pages"]},
            "api_origin": app_map["api"]["api_origin"],
            "api": api,
            "texts_seen_only_in_source": hints,
        },
        separators=(",", ":"),
    )


def system_prompt(app_map: dict[str, Any], environment: str) -> str:
    # Stable across all plan calls -> benefits from implicit prompt caching (DECISIONS.md D16).
    return f"""You are a meticulous QA engineer writing executable black-box tests for a web app.
You write test plans that a deterministic runner executes step by step, then evaluates your checks.

{STEP_DOCS}

{CHECK_DOCS}

{DESIGN_DOCS}

ENVIRONMENT: {environment}

APP MAP (discovered by crawling the live app; only use controls, routes and API paths from here):
{_compact_map(app_map)}

{FEW_SHOT}"""


def plan_prompt(req: Requirement, depth: str) -> str:
    scope = (
        "DEEP coverage: write 2-4 plans covering normal, negative and boundary behavior where the "
        "requirement allows, each verifying side effects."
        if depth == "deep"
        else "LIGHT coverage: write exactly 1 plan for the main normal behavior with its key checks."
    )
    return f"""Write test plans for this requirement.

id: {req.id}
statement: {req.statement}
PRD quote (the source of truth): "{req.prd_quote}"
observable outcomes: {json.dumps(req.observable_outcomes)}
subjective aspect: {req.subjective_aspect or "none"}

{scope}
Return requirement_id="{req.id}"."""


def critique_prompt(req: Requirement, plans: PlanSet) -> str:
    return f"""Review these test plans BEFORE they run.

Requirement {req.id}. PRD quote (source of truth): "{req.prd_quote}"

Plans:
{plans.model_dump_json()}

For every check ask: does the expectation follow from the PRD quote exactly, without assuming
behavior the PRD doesn't state? Typical mistakes: wrong timezone for dates, treating inclusive
ranges as exclusive, expecting exact text the PRD never specifies, relying on seed values,
treating internal ids/URLs as "exposed" data, checks that pass even when the feature is broken
(e.g. len() of an API body object instead of its nested list; a key name that doesn't exist),
missing BEFORE/AFTER observations for side effects, variables used before being saved.
Mutation test each check in your head: "if the developer broke exactly this PRD property, would this
check fail?" Every property word in the quote (masked, two decimals, inclusive, only, every...) must
have such a check, and filter tests must use data that contains rows the filter should exclude.
If the PRD quote lists several items (filters, fields, input classes), check that every one of them
is exercised by some plan.
If everything is right, set faithful_to_prd=true and revised=null. Otherwise list the issues and
return the corrected full plan set in `revised`."""


def revise_prompt(req: Requirement, plan: TestPlan, problem: str) -> str:
    return f"""A test plan for requirement {req.id} could not settle the requirement. Write ONE replacement plan.

PRD quote (source of truth): "{req.prd_quote}"
statement: {req.statement}

Previous plan:
{plan.model_dump_json()}

What went wrong (from the runner / validator / reviewer):
{problem}

Fix the cause (e.g. use a locator that exists in the app map, add a missing observation, correct an
expectation that does not follow from the PRD). Return requirement_id="{req.id}" and exactly 1 plan."""


# ------------------------------------------------------------------------------ validate


class MapIndex:
    def __init__(self, app_map: dict[str, Any]) -> None:
        self.routes = set(app_map["pages"])
        self.testids: set[str] = set()
        self.names: set[str] = set()
        for pg in app_map["pages"].values():
            for el in pg["elements"]:
                if el.get("testid"):
                    self.testids.add(el["testid"])
                for key in ("name", "text"):
                    if el.get(key):
                        self.names.add(el[key].lower())
                for opt in el.get("options", []):
                    self.names.add(opt["label"].lower())
        for hint in app_map["repo_hints"]["hints"]:
            if hint["kind"] == "testid":
                self.testids.add(hint["value"])
            else:
                self.names.add(hint["value"].lower())
        self.api_paths = [e["path"] for e in app_map["api"]["endpoints"]]

    def knows_name(self, text: str) -> bool:
        t = text.lower().strip()
        return any(t in n or n in t for n in self.names if n)

    def knows_api(self, path: str) -> bool:
        pattern = route_pattern(path.split("?")[0])
        for known in self.api_paths:
            k = known.replace("{account_id}", "{id}")
            if k == pattern or k.split("{")[0].rstrip("/") == pattern.rstrip("/"):
                return True
        return False


ROLES_WITHOUT_NAME = {"alert", "status", "table", "heading", "combobox", "textbox", "navigation", "main"}


def validate_plan(plan: TestPlan, index: MapIndex) -> list[str]:
    """Return a list of problems; empty means the plan may run."""
    problems: list[str] = []
    saved: set[str] = {"param"} if plan.parameter_set != "none" else set()
    for i, step in enumerate(plan.steps, 1):
        where = f"step {i} ({step.action})"
        try:
            missing = template_variables(step.value) - saved
        except ExprError as exc:
            problems.append(f"{where}: bad template: {exc}")
            missing = set()
        if missing:
            problems.append(f"{where}: uses {sorted(missing)} before they are saved")
        needs_target = step.action in {
            "click", "fill", "select", "press", "read_text", "read_all_text", "read_count",
            "read_value", "read_table",
        }
        if needs_target and (step.by == "none" or not step.locator):
            problems.append(f"{where}: needs by + locator")
        if step.by == "testid" and step.locator and step.locator not in index.testids:
            problems.append(f"{where}: testid {step.locator!r} is not in the app map")
        if step.by == "role" and step.name and not index.knows_name(step.name) and not step.optional:
            problems.append(f"{where}: no element named {step.name!r} in the app map")
        if step.by == "role" and not step.name and step.locator not in ROLES_WITHOUT_NAME and needs_target \
                and step.action not in {"read_count", "read_all_text"}:
            problems.append(f"{where}: role {step.locator!r} needs a name")
        if step.by == "label" and step.locator and not index.knows_name(step.locator):
            problems.append(f"{where}: no control labelled {step.locator!r} in the app map")
        if step.action == "read_api" and step.value and "{{" not in step.value and not index.knows_api(step.value):
            problems.append(f"{where}: API path {step.value!r} is not in the API description")
        if step.action == "goto" and not step.value:
            problems.append(f"{where}: goto needs a path")
        if step.action == "goto" and step.value and step.value.startswith(("http://", "https://")):
            problems.append(f"{where}: goto must use an app path like '/' or '/transfer', not an absolute "
                            f"URL ({step.value!r}); the API origin is only for read_api")
        if step.action.startswith("read_") or step.action == "measure_overflow":
            if not step.save_as:
                problems.append(f"{where}: read without save_as")
            else:
                saved.add(step.save_as)
    if not plan.checks:
        problems.append("plan has no checks")
    for check in plan.checks:
        try:
            missing = free_variables(check.expression) - saved
        except ExprError as exc:
            problems.append(f"check {check.description!r}: {exc}")
            continue
        if missing:
            problems.append(f"check {check.description!r}: variables {sorted(missing)} are never saved")
        if check.kind == "subjective" and (not check.subjective_question or not check.subject_var):
            problems.append(f"check {check.description!r}: subjective check needs question + subject_var")
        if check.subject_var and check.subject_var not in saved:
            problems.append(f"check {check.description!r}: subject_var {check.subject_var!r} is never saved")
    return problems


# ------------------------------------------------------------------------------ model calls


class Planner:
    def __init__(self, llm: LLM, app_map: dict[str, Any], environment: str) -> None:
        self.llm = llm
        self.system = system_prompt(app_map, environment)
        self.index = MapIndex(app_map)

    def plan(self, req: Requirement, depth: str) -> tuple[PlanSet, dict[str, Any]]:
        planset: PlanSet = self.llm.generate(
            "plan", plan_prompt(req, depth), system=self.system, schema=PlanSet, label=req.id
        ).parsed
        critique: Critique = self.llm.generate(
            "critique", critique_prompt(req, planset), system=self.system, schema=Critique, label=req.id
        ).parsed
        notes = {"critique_ok": critique.faithful_to_prd, "critique_issues": critique.issues}
        if not critique.faithful_to_prd and critique.revised and critique.revised.plans:
            planset = critique.revised
            notes["critique_applied"] = True
        if depth == "light":
            planset.plans = planset.plans[:1]
        return planset, notes

    def revise(self, req: Requirement, plan: TestPlan, problem: str) -> TestPlan | None:
        result = self.llm.generate(
            "revise", revise_prompt(req, plan, problem), system=self.system, schema=PlanSet, label=req.id
        ).parsed
        return result.plans[0] if result.plans else None
