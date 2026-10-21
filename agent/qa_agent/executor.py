"""EXECUTE (code) + RECOVER (small model, <=2 retries per step; DECISIONS.md D10).

Runs one test plan in a fresh browser context, step by step, capturing evidence: a screenshot
after every state-changing step, every saved value, the network calls each step triggered, and
console errors. A failing step gets up to two model-proposed fixes from a compact page snapshot
(ReAct-style: observe -> diagnose -> one corrective action).
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Browser, BrowserContext, Locator, Page
from playwright.sync_api import Error as PlaywrightError

from .browser import VIEWPORT, NetworkLog, settle, snapshot
from .expr import ExprError, render
from .llm import LLM, LLMError
from .schemas import RecoveryAction, Step, TestPlan

ACTION_TIMEOUT_MS = 6000
MAX_RECOVERIES_PER_STEP = 2
MUTATING = {"start_session", "goto", "click", "fill", "select", "press", "reload", "back", "set_viewport"}
RESET_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "reset_db.sh"

TABLE_JS = """(table) => {
  // textContent, not innerText: innerText applies CSS text-transform (e.g. uppercase headers)
  const headers = [...table.querySelectorAll('thead th')].map(th => th.textContent.replace(/\\s+/g, ' ').trim());
  return [...table.querySelectorAll('tbody tr')].map(tr => {
    const cells = [...tr.querySelectorAll('td,th')].map(td => td.innerText.replace(/\\s+/g, ' ').trim());
    const row = {};
    cells.forEach((c, i) => { row[headers[i] || `col${i}`] = c; });
    return row;
  });
}"""

RECOVER_SYSTEM = """You fix ONE failing step of an automated browser test. You see the step, the error
and a compact snapshot of the current page (its interactive elements with roles, names, test ids).
Diagnose the cause, then either replace the step with a corrected one that does the SAME thing
(e.g. a locator that exists on this page), ask to wait and retry (page still loading), or give up
if the page genuinely does not offer that action. Never change what the test is trying to verify."""


class StepFailedError(Exception):
    pass


@dataclass
class ScenarioRun:
    variables: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    failed_step: dict[str, Any] | None = None
    recoveries: int = 0
    duration_s: float = 0.0

    @property
    def completed(self) -> bool:
        return self.failed_step is None


@dataclass
class Environment:
    target_url: str
    api_origin: str
    entry: dict[str, Any] | None
    database_url: str | None
    run_dir: Path

    @property
    def can_reset(self) -> bool:
        return self.database_url is not None and urlparse(self.target_url).hostname in {"localhost", "127.0.0.1"}


def reset_state(env: Environment) -> bool:
    """Restore the seeded database (local targets only)."""
    if not env.can_reset:
        return False
    result = subprocess.run(  # fixed script path, argument list, no shell
        [str(RESET_SCRIPT)],
        env={"DEMOBANK_DATABASE_URL": env.database_url or "", "PATH": "/opt/homebrew/bin:/usr/bin:/bin"},
        capture_output=True, text=True, timeout=60, check=False,
    )
    return result.returncode == 0


class Executor:
    def __init__(self, browser: Browser, env: Environment, llm: LLM | None, label: str) -> None:
        self.browser = browser
        self.env = env
        self.llm = llm
        self.label = label

    # ---------------------------------------------------------------- locate

    def _locate(self, page: Page, step: Step) -> Locator:
        loc = step.locator or ""
        if step.by == "testid":
            return page.get_by_test_id(loc)
        if step.by == "role":
            return page.get_by_role(loc, name=step.name) if step.name else page.get_by_role(loc)  # type: ignore[arg-type]
        if step.by == "label":
            return page.get_by_label(loc)
        if step.by == "text":
            return page.get_by_text(loc)
        if step.by == "placeholder":
            return page.get_by_placeholder(loc)
        raise StepFailedError(f"step needs a locator (by={step.by})")

    # ---------------------------------------------------------------- actions

    def _perform(self, page: Page, context: BrowserContext, step: Step, value: str | None) -> Any:
        a = step.action
        if a == "start_session":
            page.goto(self.env.target_url)
            settle(page)
            entry = self.env.entry or {}
            if entry.get("by") == "role":
                page.get_by_role(entry["locator"], name=entry["name"], exact=True).first.click(timeout=ACTION_TIMEOUT_MS)
            settle(page)
            return None
        if a == "goto":
            page.goto(urljoin(self.env.target_url, value or "/"))
            settle(page)
            return None
        if a == "reload":
            page.reload()
            settle(page)
            return None
        if a == "back":
            page.go_back()
            settle(page)
            return None
        if a == "clear_cookies":
            context.clear_cookies()
            return None
        if a == "set_viewport":
            w, h = (int(x) for x in (value or "1280x800").lower().split("x"))
            page.set_viewport_size({"width": w, "height": h})
            settle(page)
            return None
        if a == "read_url":
            parsed = urlparse(page.url)
            return parsed.path + (f"?{parsed.query}" if parsed.query else "")
        if a == "read_api":
            response = page.request.get(urljoin(self.env.api_origin, value or "/"))
            try:
                body = response.json()
            except (PlaywrightError, json.JSONDecodeError):
                body = None
            return {"status": response.status, "body": body}
        if a == "measure_overflow":
            return page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth")
        if a == "wait_for_text":
            page.get_by_text(value or "").first.wait_for(state="visible", timeout=ACTION_TIMEOUT_MS)
            return None

        locator = self._locate(page, step)
        if a == "read_count":
            settle(page, 2000)
            return locator.count()
        if a == "read_all_text":
            settle(page, 2000)
            return [t.strip() for t in locator.all_inner_texts()]
        target = locator.first
        if step.optional and a.startswith("read_"):
            try:
                target.wait_for(state="visible", timeout=2500)
            except PlaywrightError:
                return None
        if a == "click":
            target.click(timeout=ACTION_TIMEOUT_MS)
            settle(page)
            return None
        if a == "fill":
            target.fill(value or "", timeout=ACTION_TIMEOUT_MS)
            return None
        if a == "press":
            target.press(value or "Enter", timeout=ACTION_TIMEOUT_MS)
            settle(page)
            return None
        if a == "select":
            options = target.evaluate("el => [...el.options].map(o => ({value: o.value, label: o.label}))")
            wanted = (value or "").lower()
            match = next((o for o in options if o["label"].lower() == wanted), None) or next(
                (o for o in options if wanted and wanted in o["label"].lower()), None
            ) or next((o for o in options if o["value"].lower() == wanted), None)
            if match is None:
                raise StepFailedError(f"no option matching {value!r}; options: {[o['label'] for o in options]}")
            target.select_option(value=match["value"], timeout=ACTION_TIMEOUT_MS)
            settle(page)
            return None
        if a == "read_text":
            return target.inner_text(timeout=ACTION_TIMEOUT_MS).strip()
        if a == "read_value":
            return target.evaluate(
                "el => el.tagName === 'SELECT' ? (el.selectedOptions[0]?.label ?? '') : el.value",
                timeout=ACTION_TIMEOUT_MS,
            )
        if a == "read_table":
            table = target if target.evaluate("el => el.tagName") == "TABLE" else target.locator("table").first
            return table.evaluate(TABLE_JS, timeout=ACTION_TIMEOUT_MS)
        raise StepFailedError(f"unknown action {a!r}")

    # ---------------------------------------------------------------- recover

    def _recover(self, page: Page, step: Step, error: str) -> RecoveryAction | None:
        if self.llm is None:
            return None
        snap = snapshot(page)
        compact = [
            {k: v for k, v in el.items() if k in ("role", "name", "testid", "type")} for el in snap["elements"]
        ]
        prompt = (
            f"Failing step: {step.model_dump_json()}\nError: {error[:500]}\n"
            f"Current URL: {urlparse(page.url).path}\nPage elements: {json.dumps(compact, separators=(',', ':'))}"
        )
        try:
            return self.llm.generate(
                "recover", prompt, system=RECOVER_SYSTEM, schema=RecoveryAction, label=self.label
            ).parsed
        except LLMError:
            return None

    # ---------------------------------------------------------------- run

    def run(self, plan: TestPlan, param: str | None, tag: str) -> ScenarioRun:
        run = ScenarioRun()
        if param is not None:
            run.variables["param"] = param
        start = time.monotonic()
        context = self.browser.new_context(viewport=VIEWPORT, locale="en-US")
        page = context.new_page()
        net = NetworkLog()
        net.attach(page)
        page.on("console", lambda m: run.console_errors.append(m.text) if m.type == "error" else None)
        try:
            for i, original in enumerate(plan.steps, 1):
                step = original
                attempts = 0
                while True:
                    record: dict[str, Any] = {"n": i, "action": step.action, "by": step.by,
                                              "locator": step.locator, "name": step.name}
                    mark = net.mark()
                    try:
                        value = render(step.value, run.variables)
                        record["value"] = value
                        result = self._perform(page, context, step, value)
                        if step.save_as:
                            run.variables[step.save_as] = result
                            record["saved"] = {step.save_as: _preview(result)}
                        record["ok"] = True
                    except (PlaywrightError, StepFailedError, ExprError, ValueError) as exc:
                        error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}"
                        record.update(ok=False, error=error)
                        if step.optional and step.save_as:
                            run.variables[step.save_as] = None
                            record.update(ok=True, note="optional element absent")
                        elif attempts < MAX_RECOVERIES_PER_STEP and isinstance(exc, (PlaywrightError, StepFailedError)):
                            attempts += 1
                            fix = self._recover(page, step, error)
                            record["recovery"] = fix.model_dump() if fix else None
                            run.steps.append(record | {"url": urlparse(page.url).path})
                            if fix and fix.decision == "replace_step" and fix.replacement:
                                run.recoveries += 1
                                step = fix.replacement
                                if original.save_as and not step.save_as:
                                    step.save_as = original.save_as
                                continue
                            if fix and fix.decision == "wait_and_retry":
                                run.recoveries += 1
                                settle(page, 8000)
                                continue
                            run.failed_step = record
                            break
                        else:
                            run.failed_step = record
                    record["url"] = urlparse(page.url).path
                    record["network"] = [f"{n['method']} {n['pattern']} {n['status']}" for n in net.since(mark)]
                    if step.action in MUTATING or not record["ok"]:
                        shot = self.env.run_dir / "screenshots" / f"{tag}-s{i:02d}.jpg"
                        try:
                            page.screenshot(path=str(shot), type="jpeg", quality=60, full_page=True)
                            record["screenshot"] = str(shot.relative_to(self.env.run_dir))
                            run.screenshots.append(record["screenshot"])
                        except PlaywrightError:
                            pass
                    run.steps.append(record)
                    break
                if run.failed_step is not None:
                    break
            final = self.env.run_dir / "screenshots" / f"{tag}-final.jpg"
            try:
                page.screenshot(path=str(final), type="jpeg", quality=60, full_page=True)
                run.screenshots.append(str(final.relative_to(self.env.run_dir)))
            except PlaywrightError:
                pass
        finally:
            context.close()
        run.duration_s = round(time.monotonic() - start, 2)
        return run


def _preview(value: Any, limit: int = 400) -> Any:
    text = json.dumps(value, default=str)
    return value if len(text) <= limit else text[:limit] + "…"
