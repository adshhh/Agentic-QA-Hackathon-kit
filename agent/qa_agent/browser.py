"""Shared Playwright helpers: browser launch, compact page snapshots and network capture.

Snapshots are deliberately compact (interactive elements, headings, live regions, test ids) so
they can be handed to a model cheaply (DECISIONS.md D16, "compact page snapshot").
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Page, Response, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

VIEWPORT = {"width": 1280, "height": 800}
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)

# Collects visible, test-relevant elements. Repeated test ids (e.g. table rows) are collapsed
# into one entry with a count so the snapshot stays small.
SNAPSHOT_JS = r"""
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim().slice(0, 120);
  const accName = (el) => {
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'));
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) return clean(l.innerText);
    }
    const wrap = el.closest('label');
    if (wrap) return clean(wrap.innerText);
    return clean(el.innerText || el.value || el.getAttribute('placeholder') || el.getAttribute('title'));
  };
  const implicitRole = (el) => {
    const tag = el.tagName.toLowerCase();
    if (el.getAttribute('role')) return el.getAttribute('role');
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'table') return 'table';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      return ({checkbox: 'checkbox', radio: 'radio', submit: 'button', button: 'button'})[t] || 'textbox';
    }
    return null;
  };
  const items = [];
  const seenTestIds = {};
  const selector = 'a[href],button,input,select,textarea,[role=button],[role=link],[role=alert],' +
                   '[role=status],[data-testid],h1,h2,h3,table,nav[aria-label],form';
  for (const el of document.querySelectorAll(selector)) {
    if (!visible(el)) continue;
    const testid = el.getAttribute('data-testid');
    if (testid && seenTestIds[testid]) { seenTestIds[testid].count += 1; continue; }
    const tag = el.tagName.toLowerCase();
    const item = { tag, role: implicitRole(el), name: accName(el) };
    if (testid) { item.testid = testid; item.count = 1; seenTestIds[testid] = item; }
    if (el.id) item.id = el.id;
    if (tag === 'a') item.href = el.getAttribute('href');
    if (tag === 'input') item.type = (el.getAttribute('type') || 'text').toLowerCase();
    if (tag === 'input' || tag === 'textarea') item.value = el.value;
    if (tag === 'select') {
      item.options = [...el.options].map(o => ({ value: o.value, label: clean(o.label) }));
      item.selected = el.value;
    }
    if (tag === 'form' || tag === 'nav') {
      item.name = clean(el.getAttribute('aria-label')) || item.name.slice(0, 40);
    }
    if (el.disabled) item.disabled = true;
    if (el.closest('form') && tag !== 'form') item.in_form = clean(el.closest('form').getAttribute('aria-label')) || true;
    if (testid && !['a', 'button', 'input', 'select', 'textarea'].includes(tag)) item.text = clean(el.innerText);
    items.push(item);
  }
  return { url: location.href, title: document.title, elements: items };
}
"""


def route_pattern(url: str) -> str:
    """'/accounts/<uuid>?x=1' -> '/accounts/{id}' (query dropped)."""
    return UUID_RE.sub("{id}", urlparse(url).path) or "/"


@dataclass
class NetworkLog:
    entries: list[dict[str, Any]] = field(default_factory=list)

    def attach(self, page: Page) -> None:
        page.on("response", self._on_response)

    def _on_response(self, response: Response) -> None:
        request = response.request
        if request.resource_type not in ("fetch", "xhr"):
            return
        parsed = urlparse(response.url)
        self.entries.append(
            {
                "method": request.method,
                "origin": f"{parsed.scheme}://{parsed.netloc}",
                "path": parsed.path,
                "pattern": UUID_RE.sub("{id}", parsed.path),
                "query": parsed.query,
                "status": response.status,
            }
        )

    def mark(self) -> int:
        return len(self.entries)

    def since(self, mark: int) -> list[dict[str, Any]]:
        return self.entries[mark:]


def snapshot(page: Page) -> dict[str, Any]:
    return page.evaluate(SNAPSHOT_JS)


def settle(page: Page, timeout_ms: int = 5000) -> bool:
    """Wait for the network to go idle and loading indicators to disappear.

    Returns False if the page was still busy after the timeout (callers record that as evidence
    rather than failing: a page that never settles is itself an observation).
    """
    settled = True
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PlaywrightTimeout:
        settled = False
    try:
        page.wait_for_function(
            "() => !document.querySelector('[aria-busy=true], .state--loading')", timeout=timeout_ms
        )
    except PlaywrightTimeout:
        settled = False
    return settled


@contextmanager
def browser_session(headless: bool = True, slow_mo: int = 0) -> Iterator[tuple[Browser, BrowserContext, Page]]:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, slow_mo=slow_mo)
        context = browser.new_context(viewport=VIEWPORT, locale="en-US")
        page = context.new_page()
        try:
            yield browser, context, page
        finally:
            context.close()
            browser.close()
