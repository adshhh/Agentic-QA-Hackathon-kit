from datetime import date
from decimal import Decimal

import pytest

from qa_agent.expr import (
    ExprError,
    MissingVariableError,
    api_date,
    evaluate,
    free_variables,
    money,
    render,
    ui_date,
)


def test_money_parses_ui_formats():
    assert money("$4,369.75") == Decimal("4369.75")
    assert money("−$10.00") == Decimal("-10.00")
    assert money("+$5.00 USD") == Decimal("5.00")


def test_balance_unchanged_check():
    v = {"src_before": "$4,369.75", "src_after": "$4,369.75"}
    assert evaluate("money(src_after) == money(src_before)", v) is True


def test_comprehension_over_table_rows():
    rows = [{"Type": "Deposit"}, {"Type": "Deposit"}]
    assert evaluate('all(lower(r["Type"]) == "deposit" for r in rows)', {"rows": rows}) is True
    assert free_variables('all(lower(r["Type"]) == "deposit" for r in rows)') == {"rows"}


@pytest.mark.parametrize(
    "bad",
    ["().__class__", "__import__('os')", "open('x')", "(lambda: 1)()", "_secret", "rows.pop()"],
)
def test_sandbox_rejects_escapes(bad):
    with pytest.raises(ExprError):
        evaluate(bad, {"rows": [], "_secret": 1})


def test_missing_variable_is_distinguishable():
    with pytest.raises(MissingVariableError):
        evaluate("money(nope) > 0", {})


def test_runtime_error_becomes_expr_error():
    with pytest.raises(ExprError):
        evaluate("money(x) + 1", {"x": None})


def test_render_templates_balance_plus_one_cent():
    assert render("{{money(bal) + dec('0.01')}}", {"bal": "$4,369.75"}) == "4369.76"


def test_dates_use_pacific_time():
    assert api_date("2025-03-05T02:45:00Z") == date(2025, 3, 4)
    assert ui_date("Mar 4, 2025, 6:45 PM PST") == date(2025, 3, 4)
