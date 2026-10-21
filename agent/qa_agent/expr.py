"""A small, sandboxed expression language for model-written checks and step templates.

Checks are written by the model (DECISIONS.md D6) but evaluated by code. Expressions are parsed
with `ast` and only a whitelist of node types and helper functions is allowed: no attribute
access, no lambdas, no imports, no dunder names. Evaluation runs with empty builtins.
"""

from __future__ import annotations

import ast
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

APP_TZ = ZoneInfo("America/Los_Angeles")


class ExprError(Exception):
    """The expression is malformed or uses something outside the sandbox."""


class MissingVariableError(ExprError):
    """The expression refers to a variable no step saved (the run is inconclusive, not failed)."""


# ------------------------------------------------------------------------------- helpers


def money(value: Any) -> Decimal:
    """'$4,369.75' / '−$10.00' / '+$5' / '4369.75 USD' / 12 -> Decimal."""
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    s = str(value).strip().replace("−", "-").replace("–", "-")
    negative = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    digits = re.sub(r"[^0-9.]", "", s)
    if not digits or digits.count(".") > 1:
        raise ExprError(f"money(): cannot parse {value!r}")
    try:
        amount = Decimal(digits)
    except InvalidOperation as exc:
        raise ExprError(f"money(): cannot parse {value!r}") from exc
    return -amount if negative else amount


def contains(haystack: Any, needle: Any) -> bool:
    """Case-insensitive substring test; a list haystack matches if any item contains the needle."""
    if haystack is None:
        return False
    if isinstance(haystack, (list, tuple)):
        return any(contains(h, needle) for h in haystack)
    return str(needle).lower() in str(haystack).lower()


def matches(text: Any, pattern: str) -> bool:
    return text is not None and re.search(pattern, str(text), flags=re.IGNORECASE) is not None


def lower(text: Any) -> str:
    return str(text).lower()


def strip(text: Any) -> str:
    return str(text).strip()


def startswith(text: Any, prefix: str) -> bool:
    return str(text).startswith(prefix)


def get(container: Any, key: Any, default: Any = None) -> Any:
    """Safe access; string keys of dicts also match case-insensitively ('Description' ~ 'DESCRIPTION')."""
    try:
        return container[key]
    except (KeyError, IndexError, TypeError):
        if isinstance(container, dict) and isinstance(key, str):
            for k, v in container.items():
                if isinstance(k, str) and k.lower() == key.lower():
                    return v
        return default


def col(rows: list[dict[str, Any]], name: str) -> list[Any]:
    """Values of one column from read_table rows (column names matched case-insensitively)."""
    out = []
    for row in rows or []:
        key = next((k for k in row if k.lower() == name.lower()), None)
        out.append(row.get(key) if key else None)
    return out


def ui_date(text: Any) -> date:
    """Date part of a displayed timestamp like 'Feb 14, 2025, 10:15 AM PST' or 'Feb 14, 2025'."""
    m = re.search(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})", str(text))
    if not m:
        raise ExprError(f"ui_date(): cannot parse {text!r}")
    return datetime.strptime(" ".join(m.groups()), "%b %d %Y").date()


def api_date(iso: Any) -> date:
    """Calendar date of an API ISO-8601 UTC timestamp, in the app's timezone (America/Los_Angeles)."""
    dt = datetime.fromisoformat(str(iso))  # Python 3.11+ accepts a trailing "Z"
    return dt.astimezone(APP_TZ).date()


def to_date(text: Any) -> date:
    """'2025-03-04' -> date."""
    return date.fromisoformat(str(text).strip()[:10])


def dec(value: Any) -> Decimal:
    return Decimal(str(value))


HELPERS: dict[str, Any] = {
    "money": money, "dec": dec, "contains": contains, "matches": matches, "lower": lower,
    "strip": strip, "startswith": startswith, "get": get, "col": col, "ui_date": ui_date,
    "api_date": api_date, "to_date": to_date,
    "len": len, "all": all, "any": any, "sum": sum, "min": min, "max": max, "abs": abs,
    "sorted": sorted, "set": set, "list": list, "str": str, "int": int, "float": float,
    "round": round, "bool": bool,
}

HELPER_DOCS = """Helpers available in expressions (no attribute access; use these instead):
  money(x)        '$4,369.75' / '−$10.00' / '+$5.00' / 12.5 -> Decimal (sign kept)
  dec(x)          Decimal(str(x)), for API decimal strings such as "4369.75"
  contains(h, n)  case-insensitive substring; h may be a list (true if any item contains n)
  matches(t, re)  case-insensitive regex search
  lower(t), strip(t), startswith(t, prefix)
  get(container, key, default=None)   safe dict/list access
  col(rows, "Column header")          list of one column's values from read_table rows
  ui_date(t)      date of a displayed timestamp like 'Feb 14, 2025, 10:15 AM PST'
  api_date(iso)   calendar date (America/Los_Angeles) of an API ISO timestamp
  to_date('2025-03-04') -> date
  len, all, any, sum, min, max, abs, sorted, set, list, str, int, float, round, bool
  Comprehensions are allowed: all(lower(r["Type"]) == "deposit" for r in rows)"""

# ------------------------------------------------------------------------------- sandbox

_ALLOWED = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div,
    ast.Mod, ast.FloorDiv, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd, ast.Compare, ast.Eq, ast.NotEq,
    ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot, ast.Call, ast.keyword,
    ast.Name, ast.Load, ast.Store, ast.Constant, ast.List, ast.Tuple, ast.Set, ast.Dict,
    ast.Subscript, ast.Slice, ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.comprehension,
    ast.IfExp, ast.JoinedStr, ast.FormattedValue,
)


def _comprehension_targets(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.comprehension):
            names |= {n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)}
    return names


def parse(expression: str) -> ast.Expression:
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"syntax error in {expression!r}: {exc.msg}") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ExprError(f"{type(node).__name__} is not allowed in {expression!r}")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise ExprError(f"name {node.id!r} is not allowed")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in HELPERS):
            raise ExprError(f"only helper functions may be called in {expression!r}")
    return tree


def free_variables(expression: str) -> set[str]:
    """Variable names the expression reads (excluding helpers and comprehension targets)."""
    tree = parse(expression)
    loaded = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return loaded - set(HELPERS) - _comprehension_targets(tree) - {"True", "False", "None"}


def evaluate(expression: str, variables: dict[str, Any]) -> Any:
    tree = parse(expression)
    missing = free_variables(expression) - set(variables)
    if missing:
        raise MissingVariableError(f"undefined variable(s) {sorted(missing)} in {expression!r}")
    # Globals (not locals): comprehensions have their own scope and only see globals.
    env = {"__builtins__": {}, **HELPERS, **variables}
    try:
        return eval(compile(tree, "<check>", "eval"), env)  # AST was whitelisted by parse()
    except ExprError:
        raise
    except Exception as exc:  # runtime errors inside a check (e.g. None arithmetic) -> inconclusive
        raise ExprError(f"{type(exc).__name__} evaluating {expression!r}: {exc}") from exc


TEMPLATE_RE = re.compile(r"\{\{(.+?)\}\}")


def render(template: str | None, variables: dict[str, Any]) -> str | None:
    """Substitute {{expression}} placeholders in step values."""
    if template is None:
        return None

    def _sub(match: re.Match[str]) -> str:
        value = evaluate(match.group(1), variables)
        if isinstance(value, Decimal):
            return f"{value:.2f}" if value == value.quantize(Decimal("0.01")) else str(value)
        return str(value)

    return TEMPLATE_RE.sub(_sub, template)


def template_variables(template: str | None) -> set[str]:
    if not template:
        return set()
    out: set[str] = set()
    for m in TEMPLATE_RE.finditer(template):
        out |= free_variables(m.group(1))
    return out
