"""VERIFY (code), subjective JUDGE (text-only model call, D19) and REVIEW part 2 (model, D8).

Outcome of one scenario run:
  PASS          every check evaluated to True
  FAIL          at least one check evaluated to False (a candidate; REVIEW decides if it stands)
  INCONCLUSIVE  the run did not complete, or a check could not be evaluated (missing value,
                unparsable text). Never reported as FAIL.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .executor import ScenarioRun
from .expr import ExprError, MissingVariableError, evaluate, free_variables
from .llm import LLM, LLMError
from .schemas import Check, JudgeAnswer, Requirement, ReviewVote, TestPlan

JUDGE_SYSTEM = """You judge one narrow question about text observed in a web application during a test.
Answer strictly from the quoted text. Do not assume anything that is not in the text."""

REVIEW_SYSTEM = """You are a skeptical QA lead reviewing a FAILED automated check before it is reported
as a product defect. False defect reports are costly. Decide two things independently:
1. expectation_matches_prd: does the check's expectation follow from the PRD quote, read literally
   and fairly (correct timezone, inclusive/exclusive ranges, no invented requirements, no treating
   internal identifiers or URLs as user-facing data unless the PRD says so)?
2. real_mismatch: does the observed evidence show the app genuinely violating that expectation
   (not a test-harness artefact such as reading the wrong element)?"""

REVIEW_VOTES = 3  # self-consistency (DECISIONS.md D16)


@dataclass
class CheckResult:
    description: str
    kind: str
    expression: str
    status: str  # pass | fail | error
    detail: str = ""
    observed: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScenarioVerdict:
    outcome: str  # PASS | FAIL | INCONCLUSIVE
    checks: list[CheckResult]
    reason: str


def _observed(check: Check, variables: dict[str, Any]) -> dict[str, Any]:
    try:
        names = free_variables(check.expression)
    except ExprError:
        names = set()
    if check.subject_var:
        names.add(check.subject_var)
    out = {}
    for n in sorted(names):
        value = variables.get(n)
        text = json.dumps(value, default=str)
        out[n] = value if len(text) <= 600 else text[:600] + "…"
    return out


def judge_subjective(llm: LLM, check: Check, text: Any, label: str) -> JudgeAnswer | None:
    try:
        return llm.generate(
            "judge",
            f'Question: {check.subjective_question}\nObserved text: "{text}"',
            system=JUDGE_SYSTEM, schema=JudgeAnswer, label=label,
        ).parsed
    except LLMError:
        return None


def verify(run: ScenarioRun, plan: TestPlan, llm: LLM, label: str) -> ScenarioVerdict:
    if not run.completed:
        step = run.failed_step or {}
        return ScenarioVerdict("INCONCLUSIVE", [], f"run stopped at step {step.get('n')} "
                               f"({step.get('action')}): {step.get('error')}")
    results: list[CheckResult] = []
    for check in plan.checks:
        observed = _observed(check, run.variables)
        try:
            value = evaluate(check.expression, run.variables)
        except MissingVariableError as exc:
            results.append(CheckResult(check.description, check.kind, check.expression, "error", str(exc), observed))
            continue
        except ExprError as exc:
            results.append(CheckResult(check.description, check.kind, check.expression, "error", str(exc), observed))
            continue
        status = "pass" if value is True else "fail" if value is False else "error"
        detail = "" if isinstance(value, bool) else f"expression returned {value!r}, not a boolean"
        if check.kind == "subjective" and status == "pass":
            answer = judge_subjective(llm, check, run.variables.get(check.subject_var or ""), label)
            if answer is None:
                status, detail = "error", "judge call failed"
            else:
                status = "pass" if answer.answer else "fail"
                detail = f"judge: {check.subjective_question} -> {answer.answer} ({answer.reason})"
        results.append(CheckResult(check.description, check.kind, check.expression, status, detail, observed))

    if any(r.status == "error" for r in results):
        bad = next(r for r in results if r.status == "error")
        return ScenarioVerdict("INCONCLUSIVE", results, f"check could not be evaluated: {bad.description}: {bad.detail}")
    if any(r.status == "fail" for r in results):
        failed = [r.description for r in results if r.status == "fail"]
        return ScenarioVerdict("FAIL", results, "failed: " + "; ".join(failed))
    return ScenarioVerdict("PASS", results, "all checks passed")


def review_failure(llm: LLM, req: Requirement, plan: TestPlan, verdict: ScenarioVerdict) -> dict[str, Any]:
    """REVIEW part 2: majority of 3 votes on 'did the test expect what the PRD says?'."""
    failed = [r for r in verdict.checks if r.status == "fail"]
    evidence = [
        {"check": r.description, "expression": r.expression, "observed": r.observed, "detail": r.detail}
        for r in failed
    ]
    prompt = (
        f'Requirement {req.id}. PRD quote: "{req.prd_quote}"\n'
        f"Scenario: {plan.scenario}\nSteps: {json.dumps([s.model_dump(exclude_defaults=True) for s in plan.steps])}\n"
        f"Failed checks with observed values: {json.dumps(evidence, default=str)}"
    )
    votes: list[ReviewVote] = []
    for _ in range(REVIEW_VOTES):
        try:
            votes.append(llm.generate("review", prompt, system=REVIEW_SYSTEM, schema=ReviewVote,
                                      temperature=1.0, label=req.id).parsed)
        except LLMError:
            continue
    confirm = sum(v.expectation_matches_prd and v.real_mismatch for v in votes)
    tally = Counter("confirm" if (v.expectation_matches_prd and v.real_mismatch) else "reject" for v in votes)
    majority = len(votes) // 2 + 1 if votes else 1
    return {
        "votes": [v.model_dump() for v in votes],
        "tally": dict(tally),
        "confirmed": bool(votes) and confirm >= majority,
        "reasons": [v.reason for v in votes if not (v.expectation_matches_prd and v.real_mismatch)],
    }
