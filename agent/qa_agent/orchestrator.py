"""The per-assertion loop (docs/ARCHITECTURE.md):

PLAN(+critique) -> VALIDATE -> SETUP -> EXECUTE(+recover) -> VERIFY -> REVIEW -> (REVISE)

Requirement verdict:
  FAIL        any scenario's failure survived both review parts
  PASS        every scenario passed
  UNVERIFIED  otherwise, with the reason (never a claimed pass that wasn't observed)
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from playwright.sync_api import Browser

from .executor import Environment, Executor, reset_state
from .llm import LLM, LLMError
from .planner import PARAMETER_SETS, Planner, validate_plan
from .schemas import Requirement, TestPlan
from .verify import ScenarioVerdict, review_failure, verify

MAX_REVISIONS = 2  # per requirement (DECISIONS.md D9)


class RequirementTester:
    def __init__(self, llm: LLM, planner: Planner, browser: Browser, env: Environment, log=print) -> None:
        self.llm = llm
        self.planner = planner
        self.browser = browser
        self.env = env
        self.log = log

    def _execute(self, plan: TestPlan, req: Requirement, tag: str, params: list[str | None]) -> list[dict[str, Any]]:
        executor = Executor(self.browser, self.env, self.llm, label=req.id)
        runs = []
        for k, param in enumerate(params):
            reset = reset_state(self.env) if plan.needs_clean_state else False
            run = executor.run(plan, param, f"{tag}-v{k:02d}")
            verdict = verify(run, plan, self.llm, req.id)
            runs.append({"param": param, "reset": reset, "run": run, "verdict": verdict})
        return runs

    @staticmethod
    def _combine(runs: list[dict[str, Any]]) -> str:
        outcomes = [r["verdict"].outcome for r in runs]
        if "FAIL" in outcomes:
            return "FAIL"
        if "INCONCLUSIVE" in outcomes:
            return "INCONCLUSIVE"
        return "PASS"

    def _scenario(self, req: Requirement, plan: TestPlan, idx: int, budget: list[int]) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        current = plan
        for revision in range(MAX_REVISIONS + 1):
            attempt: dict[str, Any] = {"revision": revision, "plan": current.model_dump()}
            attempts.append(attempt)
            problems = validate_plan(current, self.planner.index)
            attempt["validation_problems"] = problems
            problem_for_revision: str | None = None
            if problems:
                problem_for_revision = "Plan failed validation against the app map:\n- " + "\n- ".join(problems)
            else:
                params = PARAMETER_SETS.get(current.parameter_set) or [None]
                tag = f"{req.id}-s{idx}-r{revision}"
                runs = self._execute(current, req, tag, list(params))
                outcome = self._combine(runs)
                attempt["runs"] = [_run_record(r) for r in runs]
                attempt["outcome"] = outcome
                self.log(f"    scenario {idx} rev {revision}: {outcome}  ({current.scenario})")

                if outcome == "PASS":
                    return {"status": "PASS", "reason": "all checks passed", "attempts": attempts}

                if outcome == "INCONCLUSIVE":
                    reasons = {r["verdict"].reason for r in runs if r["verdict"].outcome == "INCONCLUSIVE"}
                    problem_for_revision = "The run was inconclusive:\n- " + "\n- ".join(sorted(reasons))

                if outcome == "FAIL":
                    # REVIEW part 1 (code): re-run the failing variants once with fresh state.
                    failing = [r["param"] for r in runs if r["verdict"].outcome == "FAIL"]
                    rerun = self._execute(current, req, tag + "-rerun", failing)
                    attempt["rerun"] = [_run_record(r) for r in rerun]
                    if self._combine(rerun) != "FAIL":
                        return {"status": "UNVERIFIED", "attempts": attempts,
                                "reason": "failure did not reproduce on a fresh re-run (possible timing issue)"}
                    # REVIEW part 2 (model, majority of 3): is the expectation faithful to the PRD?
                    first_fail = _strongest_failure(runs)
                    review = review_failure(self.llm, req, current, first_fail["verdict"])
                    attempt["reviewed_param"] = first_fail["param"]
                    attempt["review"] = review
                    if review["confirmed"]:
                        return {"status": "FAIL", "reason": first_fail["verdict"].reason, "attempts": attempts,
                                "failing_params": failing}
                    problem_for_revision = ("Reviewers judged the failing check unfaithful to the PRD or the "
                                            "evidence unconvincing:\n- " + "\n- ".join(review["reasons"]))

            if budget[0] <= 0 or problem_for_revision is None:
                break
            budget[0] -= 1
            try:
                revised = self.planner.revise(req, current, problem_for_revision)
            except LLMError as exc:
                attempt["revise_error"] = str(exc)
                break
            if revised is None:
                break
            current = revised

        last = attempts[-1]
        reason = (
            "plan could not be made valid: " + "; ".join(last.get("validation_problems", [])[:3])
            if last.get("validation_problems")
            else f"no conclusive result after {len(attempts)} attempt(s); last outcome: {last.get('outcome')}"
        )
        return {"status": "UNVERIFIED", "reason": reason, "attempts": attempts}

    def test(self, req: Requirement, depth: str) -> dict[str, Any]:
        self.log(f"[{req.id}] {req.statement[:90]}")
        try:
            planset, notes = self.planner.plan(req, depth)
        except LLMError as exc:
            return {"id": req.id, "verdict": "UNVERIFIED", "reason": f"planning failed: {exc}", "scenarios": []}
        budget = [MAX_REVISIONS]
        scenarios = []
        for idx, plan in enumerate(planset.plans, 1):
            result = self._scenario(req, plan, idx, budget)
            result.update(scenario=plan.scenario, scenario_type=plan.scenario_type, technique=plan.technique)
            scenarios.append(result)
        statuses = [s["status"] for s in scenarios]
        if "FAIL" in statuses:
            verdict, reason = "FAIL", next(s["reason"] for s in scenarios if s["status"] == "FAIL")
        elif statuses and all(s == "PASS" for s in statuses):
            verdict, reason = "PASS", f"{len(statuses)} scenario(s) passed"
        else:
            verdict = "UNVERIFIED"
            reason = "; ".join(f"{s['scenario']}: {s['reason']}" for s in scenarios if s["status"] != "PASS")
        self.log(f"  => {verdict}: {reason[:160]}")
        return {
            "id": req.id, "area": req.area, "depth": depth, "statement": req.statement,
            "prd_quote": req.prd_quote, "verdict": verdict, "reason": reason,
            "planning": notes, "scenarios": scenarios,
            "cost_usd": round(sum(c.cost_usd for c in self.llm.ledger.calls if c.label == req.id), 6),
        }


OBJECTIVE_KINDS = {"side_effect": 3, "invariant": 3, "cross_source": 2, "metamorphic": 2, "functional": 1}


def _strongest_failure(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the failing run whose evidence is most objective (e.g. balances changed) for review.

    With code-generated inputs, one input may fail only because of a weak check while another
    shows money actually moving; reviewing the first failure could discard the real defect.
    """
    def score(r: dict[str, Any]) -> int:
        return sum(OBJECTIVE_KINDS.get(c.kind, 0) for c in r["verdict"].checks if c.status == "fail")

    return max((r for r in runs if r["verdict"].outcome == "FAIL"), key=score)


def _run_record(r: dict[str, Any]) -> dict[str, Any]:
    run = r["run"]
    verdict: ScenarioVerdict = r["verdict"]
    return {
        "param": r["param"],
        "db_reset": r["reset"],
        "outcome": verdict.outcome,
        "reason": verdict.reason,
        "checks": [asdict(c) for c in verdict.checks],
        "steps": run.steps,
        "recoveries": run.recoveries,
        "console_errors": run.console_errors[:10],
        "screenshots": run.screenshots,
        "duration_s": run.duration_s,
    }
