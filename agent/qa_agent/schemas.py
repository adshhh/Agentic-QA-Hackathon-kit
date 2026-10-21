"""Pydantic schemas shared by the pipeline and used as structured-output contracts for model calls.

Gemini's response_schema supports nested models, lists, optionals and Literal enums, but not
free-form dicts, so model-facing schemas avoid dict fields.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Area = Literal["access", "accounts", "transfers", "history", "profile", "general", "seed"]
Verdict = Literal["PASS", "FAIL", "UNVERIFIED"]

# ---------------------------------------------------------------------------- interpret


class Requirement(BaseModel):
    id: str = Field(description="Short stable id, e.g. TRF-04")
    area: Area
    statement: str = Field(description="One atomic, testable behavior in plain language")
    prd_quote: str = Field(description="The exact PRD sentence(s) this comes from, copied verbatim")
    observable_outcomes: list[str] = Field(
        description="What must be observed to decide pass/fail, including side effects "
        "(balances, records, redirects) that must or must not change"
    )
    subjective_aspect: str | None = Field(
        default=None,
        description="Opinion-based wording (e.g. 'clear explanation'), or null if fully objective",
    )
    verifiable_in_browser: bool = Field(
        description="False only if no reliable check is possible through the UI or its API"
    )


class RequirementSet(BaseModel):
    requirements: list[Requirement]


# ---------------------------------------------------------------------------- explore


class PageSummary(BaseModel):
    route: str
    purpose: str
    key_actions: list[str]
    readable_values: list[str]


class AppMapSummary(BaseModel):
    pages: list[PageSummary]
    entry_action: str = Field(description="How a user gets from the landing page into the app")
    notes: list[str]


# ---------------------------------------------------------------------------- plan

StepAction = Literal[
    "start_session",  # go to the landing page and perform the discovered entry action
    "goto",  # value = path, e.g. /transfer
    "click",
    "fill",  # value = text to type (may contain {{expressions}})
    "select",  # value = visible option label (or a substring of it)
    "press",  # value = key, e.g. Enter
    "reload",
    "back",
    "clear_cookies",
    "set_viewport",  # value = "375x812"
    "read_text",  # save the element's visible text
    "read_all_text",  # save a list with the text of every matching element
    "read_count",  # save how many elements match
    "read_value",  # save an input/select's current value (selects: visible label)
    "read_table",  # save table rows as a list of {column header: cell text}
    "read_url",  # save the current path + query
    "read_api",  # value = API path (GET, with the browser's session cookie); saves parsed JSON
    "wait_for_text",  # value = text that must appear on the page
    "measure_overflow",  # save true if the page scrolls horizontally
]

LocatorBy = Literal["testid", "role", "label", "text", "placeholder", "none"]


class Step(BaseModel):
    action: StepAction
    by: LocatorBy = Field(default="none", description="How to find the element; 'none' if no element")
    locator: str | None = Field(
        default=None,
        description="testid value, ARIA role (button/link/heading/alert/status/combobox/textbox...), "
        "label text, visible text or placeholder",
    )
    name: str | None = Field(default=None, description="Accessible name, only when by='role'")
    value: str | None = None
    save_as: str | None = Field(default=None, description="Variable name for read_* results")
    optional: bool = Field(default=False, description="True if the element may legitimately be absent")


CheckKind = Literal[
    "functional", "side_effect", "metamorphic", "invariant", "cross_source", "subjective"
]


class Check(BaseModel):
    description: str = Field(description="Human-readable expectation, citing the PRD")
    kind: CheckKind
    expression: str = Field(
        description="Restricted Python boolean expression over saved variables and helper functions; "
        "for kind=subjective, an expression that is true when the text to judge exists"
    )
    subjective_question: str | None = Field(
        default=None, description="kind=subjective only: a narrow yes/no question about the text"
    )
    subject_var: str | None = Field(default=None, description="kind=subjective only: variable with the text")


ParameterSet = Literal["none", "invalid_amounts", "valid_amount_boundaries"]


class TestPlan(BaseModel):
    __test__ = False  # not a pytest test class despite the name

    scenario: str = Field(description="One-line description of this scenario")
    scenario_type: Literal["normal", "negative", "boundary"]
    technique: str = Field(description="Test-design technique used, e.g. boundary-value analysis")
    needs_clean_state: bool = Field(description="True if the scenario mutates data or depends on known data")
    parameter_set: ParameterSet = Field(
        default="none",
        description="If not none, the steps run once per code-generated value, available as {{param}}",
    )
    steps: list[Step]
    checks: list[Check]


class PlanSet(BaseModel):
    requirement_id: str
    plans: list[TestPlan]


class Critique(BaseModel):
    faithful_to_prd: bool
    issues: list[str]
    revised: PlanSet | None = Field(default=None, description="Corrected plans if any issue was found")


class RecoveryAction(BaseModel):
    diagnosis: str
    decision: Literal["replace_step", "wait_and_retry", "give_up"]
    replacement: Step | None = None


class ReviewVote(BaseModel):
    expectation_matches_prd: bool
    real_mismatch: bool = Field(description="Does the observed evidence show a genuine mismatch?")
    reason: str


class JudgeAnswer(BaseModel):
    answer: bool
    reason: str


class ReportNarrative(BaseModel):
    executive_summary: str
    key_findings: list[str]
    limitations: list[str]
    recommendations: list[str]
