from qa_agent.config import Settings
from qa_agent.llm import CallRecord, CostLedger, compute_cost


def test_cost_uses_uncached_cached_and_output_rates():
    # gemini-3.8-flash: $0.75 in, $0.075 cached, $3.75 out per 1M tokens
    cost = compute_cost("gemini-3.8-flash", input_tokens=1_000_000, cached_input_tokens=400_000,
                        output_tokens=100_000)
    assert round(cost, 6) == round(0.6 * 0.75 + 0.4 * 0.075 + 0.1 * 3.75, 6)


def test_unknown_model_is_free():
    assert compute_cost("some-local-model", 5000, 0, 5000) == 0.0


def test_recover_phase_uses_light_model():
    s = Settings()
    assert s.model_for("recover") == "gemini-3.5-flash-lite"
    assert s.model_for("plan") == "gemini-3.8-flash"


def test_failed_parse_is_still_billed_and_retried_once():
    import pytest
    from pydantic import BaseModel

    from qa_agent.llm import LLM, MAX_OUTPUT_TOKENS, LLMError, ParseError

    class Out(BaseModel):
        ok: bool

    calls = []

    def fake_gemini(self, record, prompt, system, schema, temperature, cap):
        calls.append(cap)
        record.input_tokens, record.output_tokens = 1000, 16000
        raise ParseError("truncated")

    ledger = CostLedger()
    llm = LLM(Settings(), ledger)
    llm._gemini = fake_gemini.__get__(llm)
    with pytest.raises(LLMError):
        llm.generate("plan", "x", schema=Out)
    assert len(calls) == 2  # one retry on parse failure, then give up
    assert calls[0] == MAX_OUTPUT_TOKENS["plan"]  # per-phase output cap is applied
    assert all(c.cost_usd > 0 and not c.ok for c in ledger.calls)
    assert len(ledger.calls) == 2


def test_ledger_aggregates_by_phase_and_model():
    ledger = CostLedger()
    ledger.add(CallRecord(phase="plan", model="m", provider="p", input_tokens=10, output_tokens=5, cost_usd=0.1))
    ledger.add(CallRecord(phase="plan", model="m", provider="p", input_tokens=20, output_tokens=5, cost_usd=0.2))
    ledger.add(CallRecord(phase="recover", model="n", provider="p", cost_usd=0.05, ok=False))
    s = ledger.summary()
    assert s["total_calls"] == 3 and s["failed_calls"] == 1
    assert s["by_phase"]["plan"]["calls"] == 2
    assert round(s["total_cost_usd"], 6) == 0.35
