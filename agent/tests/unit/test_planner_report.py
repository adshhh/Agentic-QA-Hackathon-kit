from qa_agent.planner import MapIndex, validate_plan
from qa_agent.report import _strip_unsupported
from qa_agent.schemas import Check, Step, TestPlan

APP_MAP = {
    "pages": {
        "/transfer": {
            "route": "/transfer",
            "elements": [
                {"role": "textbox", "name": "Amount (USD)"},
                {"role": "button", "name": "Transfer funds"},
                {"role": None, "name": "$4,369.75", "testid": "transfer-balance"},
            ],
        }
    },
    "api": {"endpoints": [{"method": "GET", "path": "/api/accounts", "params": [], "responses": ["200"]}]},
    "repo_hints": {"hints": [{"kind": "text", "value": "Transfer not completed"}]},
}


def _plan(steps, checks):
    return TestPlan(scenario="s", scenario_type="negative", technique="t", needs_clean_state=True,
                    steps=steps, checks=checks)


def test_valid_plan_passes_validation():
    plan = _plan(
        [Step(action="start_session"), Step(action="goto", value="/transfer"),
         Step(action="read_text", by="testid", locator="transfer-balance", save_as="before"),
         Step(action="fill", by="label", locator="Amount (USD)", value="{{money(before) + dec('0.01')}}"),
         Step(action="click", by="role", locator="button", name="Transfer funds"),
         Step(action="read_text", by="role", locator="alert", save_as="msg", optional=True),
         Step(action="read_api", value="/api/accounts", save_as="api_after")],
        [Check(description="d", kind="side_effect", expression="msg is not None and api_after['status'] == 200")],
    )
    assert validate_plan(plan, MapIndex(APP_MAP)) == []


def test_plan_with_unknown_controls_and_unsaved_variables_is_rejected():
    plan = _plan(
        [Step(action="click", by="role", locator="button", name="Send money"),
         Step(action="read_text", by="testid", locator="nonexistent-id", save_as="x"),
         Step(action="read_api", value="/api/secret-admin", save_as="y"),
         Step(action="fill", by="label", locator="Amount (USD)", value="{{money(z)}}")],
        [Check(description="d", kind="functional", expression="money(never_saved) > 0")],
    )
    problems = " | ".join(validate_plan(plan, MapIndex(APP_MAP)))
    for expected in ("Send money", "nonexistent-id", "/api/secret-admin", "['z']", "never_saved"):
        assert expected in problems


def test_goto_to_another_origin_is_rejected():
    plan = _plan([Step(action="goto", value="http://localhost:8000/"),
                  Step(action="read_url", save_as="u")],
                 [Check(description="d", kind="functional", expression="u == '/'")])
    assert any("absolute URL" in p for p in validate_plan(plan, MapIndex(APP_MAP)))


def test_fact_check_removes_sentences_with_unsupported_numbers():
    facts = '{"balance": "-0.01", "amount": "4369.76", "count": 3}'
    text = "The overdraft left a balance of -0.01 after moving 4369.76. It also lost $999.00 somewhere."
    kept, removed = _strip_unsupported(text, facts)
    assert "4369.76" in kept
    assert removed == ["It also lost $999.00 somewhere."]
