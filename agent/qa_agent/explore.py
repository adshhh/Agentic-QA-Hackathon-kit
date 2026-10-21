"""EXPLORE: learn the app's step vocabulary (DECISIONS.md D7).

1. Crawl the live app (code): landing page, then the entry action, then every same-origin route
   reachable through links. The crawler never submits forms and avoids destructive-looking
   buttons, so exploring cannot change application data.
2. Discover the API from observed network traffic and read its OpenAPI description, if any.
3. If a repository path is given, collect labels/test ids/button texts from the frontend source
   ONLY to fill gaps (elements that appear in states the crawl didn't reach). Provenance is kept.
4. One model call summarizes each page's purpose and actions.
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Page

from .browser import NetworkLog, browser_session, route_pattern, settle, snapshot
from .llm import LLM
from .schemas import AppMapSummary

MAX_PAGES = 15
UNSAFE_BUTTON = re.compile(
    r"log ?out|sign ?out|delete|remove|submit|transfer|pay|send|apply|clear|save", re.IGNORECASE
)
UNKNOWN_ROUTE = "/__qa_probe_unknown_route__"

SUMMARY_SYSTEM = """You summarize a crawled web application for a QA test planner.
For each route, state its purpose, the user actions available (with the exact control names)
and the values a tester can read there (with their test ids where present). Identify how a user
enters the application from the landing page. Be concise and only use names present in the data."""


def _element_key(el: dict[str, Any]) -> str:
    return el.get("testid") or f"{el.get('role')}:{el.get('name')}"


def _try_entry(page: Page, start_url: str, net: NetworkLog) -> dict[str, Any] | None:
    """Click each safe-looking landing button until one navigates away: that is the entry action."""
    for el in snapshot(page)["elements"]:
        if el.get("role") != "button" or not el.get("name") or UNSAFE_BUTTON.search(el["name"]):
            continue
        mark = net.mark()
        page.get_by_role("button", name=el["name"], exact=True).first.click()
        settle(page)
        if route_pattern(page.url) != route_pattern(start_url):
            return {
                "action": "click",
                "by": "role",
                "locator": "button",
                "name": el["name"],
                "lands_on": route_pattern(page.url),
                "network": net.since(mark),
            }
        page.goto(start_url)
        settle(page)
    return None


def crawl(target_url: str, headless: bool = True) -> dict[str, Any]:
    pages: dict[str, dict[str, Any]] = {}
    net = NetworkLog()
    with browser_session(headless=headless) as (_browser, _context, page):
        net.attach(page)

        def visit(url: str) -> dict[str, Any]:
            mark = net.mark()
            page.goto(url)
            settled = settle(page)
            snap = snapshot(page)
            pattern = route_pattern(page.url)
            entry = {
                "route": pattern,
                "example_url": urlparse(page.url).path,
                "title": snap["title"],
                "elements": snap["elements"],
                "network_on_load": net.since(mark),
                "settled": settled,
                "source": "live",
            }
            pages.setdefault(pattern, entry)
            return entry

        landing = visit(target_url)
        entry = _try_entry(page, target_url, net)

        frontier: list[str] = []

        def enqueue_links(entry_page: dict[str, Any]) -> None:
            for el in entry_page["elements"]:
                href = el.get("href")
                if not href or href.startswith(("http", "mailto:", "#")):
                    continue
                if route_pattern(href) not in pages and href not in frontier:
                    frontier.append(href)

        if entry:  # the entry action left us on the first in-app page: record it without reloading
            enqueue_links(_snapshot_current(page, pages))
        enqueue_links(landing)
        while frontier and len(pages) < MAX_PAGES:
            href = frontier.pop(0)
            if route_pattern(href) in pages:
                continue
            enqueue_links(visit(urljoin(target_url, href)))

        # Probe: what does an unknown route look like inside a session?
        probe = visit(urljoin(target_url, UNKNOWN_ROUTE))
        probe["note"] = "probe of an unknown route"

    return {"pages": pages, "entry": entry, "network": net.entries}


def _snapshot_current(page: Page, pages: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Record the page the entry action landed on (without reloading it)."""
    settle(page)
    snap = snapshot(page)
    pattern = route_pattern(page.url)
    entry = {
        "route": pattern,
        "example_url": urlparse(page.url).path,
        "title": snap["title"],
        "elements": snap["elements"],
        "network_on_load": [],
        "settled": True,
        "source": "live",
    }
    pages.setdefault(pattern, entry)
    return entry


def discover_api(network: list[dict[str, Any]], target_url: str) -> dict[str, Any]:
    """Find the API origin from observed calls and read its OpenAPI document if one exists."""
    target_origin = "{0.scheme}://{0.netloc}".format(urlparse(target_url))
    origins = sorted({n["origin"] for n in network})
    api_origin = next((o for o in origins if o != target_origin), target_origin)
    observed = sorted({(n["method"], n["pattern"]) for n in network})
    openapi: dict[str, Any] | None = None
    for path in ("/openapi.json", "/api/openapi.json", "/docs/openapi.json"):
        try:
            with urllib.request.urlopen(api_origin + path, timeout=5) as resp:
                openapi = json.loads(resp.read())
                break
        except (OSError, ValueError):
            continue
    endpoints = []
    if openapi:
        components = openapi.get("components", {}).get("schemas", {})
        for p, methods in openapi.get("paths", {}).items():
            for method, spec in methods.items():
                params = [prm.get("name") for prm in spec.get("parameters", [])]
                responses = spec.get("responses", {})
                ok = next((responses[c] for c in ("200", "201") if c in responses), None)
                body_schema = (ok or {}).get("content", {}).get("application/json", {}).get("schema")
                request_schema = (
                    spec.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
                )
                endpoints.append(
                    {
                        "method": method.upper(),
                        "path": p,
                        "summary": spec.get("summary", ""),
                        "params": params,
                        "responses": sorted(responses.keys()),
                        "response_shape": schema_shape(body_schema, components) if body_schema else None,
                        "request_shape": schema_shape(request_schema, components) if request_schema else None,
                    }
                )
    return {
        "api_origin": api_origin,
        "observed_calls": [{"method": m, "pattern": p} for m, p in observed],
        "openapi_found": openapi is not None,
        "endpoints": endpoints,
    }


def schema_shape(schema: dict[str, Any], components: dict[str, Any], depth: int = 0) -> Any:
    """Compact JSON shape of an OpenAPI schema, e.g. {"transactions": [{"id": "string", ...}]}."""
    if "$ref" in schema:
        schema = components.get(schema["$ref"].split("/")[-1], {})
    if "anyOf" in schema:
        options = [s for s in schema["anyOf"] if s.get("type") != "null"]
        inner = schema_shape(options[0], components, depth) if options else "null"
        return f"{inner}|null" if isinstance(inner, str) else inner
    kind = schema.get("type")
    if kind == "array":
        return [schema_shape(schema.get("items", {}), components, depth + 1)]
    if kind == "object" or "properties" in schema:
        if depth >= 3:
            return "object"
        return {k: schema_shape(v, components, depth + 1) for k, v in schema.get("properties", {}).items()}
    if "enum" in schema:
        return "|".join(map(str, schema["enum"]))
    return kind or "any"


REPO_PATTERNS = {
    "testid": re.compile(r'data-testid="([^"]+)"'),
    "label": re.compile(r"<label[^>]*>\s*([^<{]+?)\s*<"),
    "text": re.compile(r'(?:title|label|text)[=:]\s*"([A-Z][^"]{2,60})"'),
    # User-facing sentences in string literals, e.g. client-side validation messages
    "message": re.compile(r"""["']([A-Z][^"'{}<>\n]{8,110}[.!?…])["']"""),
}


def repo_hints(repo_path: str | None, live_pages: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Strings from frontend source that the live crawl did not see (gap filling only)."""
    if not repo_path:
        return {"used": False, "hints": []}
    root = Path(repo_path)
    files = [p for p in root.rglob("*") if p.suffix in {".tsx", ".jsx", ".ts", ".js", ".vue", ".html"}
             and "node_modules" not in p.parts and "dist" not in p.parts]
    seen_live = set()
    for pg in live_pages.values():
        for el in pg["elements"]:
            seen_live.update(filter(None, [el.get("testid"), el.get("name"), el.get("text")]))
    hints: list[dict[str, str]] = []
    for f in files:
        source = f.read_text(errors="ignore")
        for kind, rx in REPO_PATTERNS.items():
            for match in rx.findall(source):
                value = match.strip()
                if value and value not in seen_live and len(hints) < 200:
                    hints.append({"kind": kind, "value": value, "file": f.name, "source": "repo"})
    unique = {(h["kind"], h["value"]): h for h in hints}
    return {"used": True, "hints": list(unique.values())}


def compact_for_model(crawl_data: dict[str, Any], api: dict[str, Any], hints: dict[str, Any]) -> str:
    pages = []
    for pg in crawl_data["pages"].values():
        pages.append(
            {
                "route": pg["route"],
                "elements": [
                    {k: v for k, v in el.items() if k in ("role", "name", "testid", "text", "type", "count", "options")}
                    for el in pg["elements"]
                ],
                "api_calls_on_load": sorted({f"{n['method']} {n['pattern']}" for n in pg["network_on_load"]}),
            }
        )
    return json.dumps(
        {
            "entry": {k: v for k, v in (crawl_data["entry"] or {}).items() if k != "network"},
            "pages": pages,
            "api_endpoints": [f"{e['method']} {e['path']} {e['params']}" for e in api["endpoints"]],
            "repo_hints": [h["value"] for h in hints["hints"]][:80],
        },
        separators=(",", ":"),
    )


def explore(llm: LLM, target_url: str, repo_path: str | None = None, headless: bool = True) -> dict[str, Any]:
    crawl_data = crawl(target_url, headless=headless)
    api = discover_api(crawl_data["network"], target_url)
    hints = repo_hints(repo_path, crawl_data["pages"])
    summary = llm.generate(
        "explore",
        "Crawl data (JSON):\n" + compact_for_model(crawl_data, api, hints),
        system=SUMMARY_SYSTEM,
        schema=AppMapSummary,
    ).parsed
    return {
        "target_url": target_url,
        "entry": crawl_data["entry"],
        "pages": crawl_data["pages"],
        "api": api,
        "repo_hints": hints,
        "summary": summary.model_dump(),
    }
