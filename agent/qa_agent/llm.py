"""Thin, provider-agnostic model wrapper with per-call token and cost accounting.

Every model call in the agent goes through `LLM.generate(phase=...)`, which:
  * routes to the model configured for that phase (DECISIONS.md D13),
  * optionally constrains output to a Pydantic schema (structured outputs, D16),
  * records tokens, cost and latency in a CostLedger (and runs/<ts>/llm_calls.jsonl).

Providers: "gemini" (google-genai SDK) and "openai_compat" (any OpenAI-compatible endpoint,
e.g. local Ollama or OpenRouter; D15).
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

from .config import PRICING_DATE, Settings, price_for
from .run_context import RunContext

T = TypeVar("T", bound=BaseModel)

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
# Output caps per phase (visible + thinking). A runaway generation is cut off cheaply and retried,
# instead of burning ~65K tokens (observed once on a plan call before these caps existed).
MAX_OUTPUT_TOKENS = {"plan": 24000, "critique": 24000, "revise": 20000, "interpret": 24000, "report": 10000}
DEFAULT_MAX_OUTPUT_TOKENS = 6000
# Thinking depth per phase (Gemini thinking_level). Plan/interpret keep the model default, the
# setting validated by the eval; follow-up phases think less: they hit output caps otherwise, and
# their task (fix, vote, judge one sentence, summarize) is narrower.
THINKING_LEVEL = {"critique": "MEDIUM", "review": "LOW", "judge": "LOW",
                  "recover": "LOW", "report": "LOW", "explore": "LOW", "ping": "LOW"}


class ParseError(Exception):
    """The model answered but the output did not match the schema (retried once)."""


def compute_cost(model: str, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> float:
    """USD cost of one call. `input_tokens` includes cached tokens; `output_tokens` includes thinking."""
    price = price_for(model)
    uncached = max(input_tokens - cached_input_tokens, 0)
    return (
        uncached * price.input + cached_input_tokens * price.cached_input + output_tokens * price.output
    ) / 1_000_000


@dataclass
class CallRecord:
    phase: str
    model: str
    provider: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0  # visible output + thinking tokens (both billed at the output rate)
    thinking_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    attempts: int = 1
    ok: bool = True
    error: str | None = None
    label: str | None = None  # free-form tag, e.g. the requirement id


class CostLedger:
    def __init__(self, run: RunContext | None = None) -> None:
        self.run = run
        self.calls: list[CallRecord] = []

    def add(self, record: CallRecord) -> None:
        self.calls.append(record)
        if self.run:
            self.run.append_jsonl("llm_calls.jsonl", asdict(record))

    def summary(self) -> dict[str, Any]:
        by_phase: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        by_model: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for c in self.calls:
            for bucket in (by_phase[c.phase], by_model[c.model]):
                bucket["calls"] += 1
                bucket["input_tokens"] += c.input_tokens
                bucket["cached_input_tokens"] += c.cached_input_tokens
                bucket["output_tokens"] += c.output_tokens
                bucket["cost_usd"] += c.cost_usd
        return {
            "pricing_date": PRICING_DATE,
            "total_calls": len(self.calls),
            "failed_calls": sum(not c.ok for c in self.calls),
            "total_input_tokens": sum(c.input_tokens for c in self.calls),
            "total_cached_input_tokens": sum(c.cached_input_tokens for c in self.calls),
            "total_output_tokens": sum(c.output_tokens for c in self.calls),
            "total_cost_usd": round(sum(c.cost_usd for c in self.calls), 6),
            "by_phase": {k: dict(v) for k, v in by_phase.items()},
            "by_model": {k: dict(v) for k, v in by_model.items()},
        }


@dataclass
class LLMResult:
    text: str
    parsed: Any
    record: CallRecord


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, settings: Settings, ledger: CostLedger) -> None:
        self.settings = settings
        self.ledger = ledger
        self._client: Any = None

    # -- public -----------------------------------------------------------------------------

    def generate(
        self,
        phase: str,
        prompt: str,
        *,
        system: str | None = None,
        schema: type[T] | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        label: str | None = None,
    ) -> LLMResult:
        model = self.settings.model_for(phase)
        provider = self.settings.provider
        call = self._gemini if provider == "gemini" else self._openai_compat
        cap = max_output_tokens or MAX_OUTPUT_TOKENS.get(phase, DEFAULT_MAX_OUTPUT_TOKENS)

        parse_retries = 0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            # One ledger record per API request, so tokens of failed attempts are still billed.
            record = CallRecord(phase=phase, model=model, provider=provider, label=label, attempts=attempt)
            start = time.monotonic()
            try:
                text, parsed = call(record, prompt, system, schema, temperature, cap)
            except Exception as exc:  # classified as retryable or fatal below
                retry = (isinstance(exc, ParseError) and parse_retries == 0) or (
                    not isinstance(exc, ParseError) and _is_retryable(exc)
                )
                parse_retries += isinstance(exc, ParseError)
                record.ok, record.error = False, f"{type(exc).__name__}: {str(exc)[:500]}"
                self._finish(record, start)
                if attempt == MAX_ATTEMPTS or not retry:
                    raise LLMError(f"{phase} call failed: {record.error}") from exc
                if not isinstance(exc, ParseError):
                    time.sleep(2**attempt)
                continue
            self._finish(record, start)
            return LLMResult(text=text, parsed=parsed, record=record)
        raise LLMError(f"{phase} call failed after {MAX_ATTEMPTS} attempts")  # pragma: no cover

    def _finish(self, record: CallRecord, start: float) -> None:
        record.cost_usd = compute_cost(record.model, record.input_tokens, record.cached_input_tokens,
                                       record.output_tokens)
        record.latency_s = round(time.monotonic() - start, 3)
        self.ledger.add(record)

    def _parse(self, record: CallRecord, schema: type[T], text: str, truncated: bool) -> T:
        try:
            return schema.model_validate_json(text)
        except ValueError as exc:
            if self.ledger.run:
                dump = self.ledger.run.path(f"llm_parse_failure-{record.phase}-{len(self.ledger.calls)}.txt")
                dump.write_text(text)
            hint = " (output hit the token cap)" if truncated else ""
            raise ParseError(f"output did not match {schema.__name__}{hint}: {str(exc)[:200]}") from exc

    # -- providers --------------------------------------------------------------------------

    def _gemini(self, record, prompt, system, schema, temperature, max_output_tokens):
        from google import genai
        from google.genai import types

        if self._client is None:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise LLMError("GEMINI_API_KEY is not set (add it to agent/.env)")
            # Timeout (ms): a request on a dead connection (e.g. after the machine slept) fails and
            # is retried instead of hanging the whole run.
            self._client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=180_000))

        level = THINKING_LEVEL.get(record.phase)
        config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level=level) if level else None,
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json" if schema else None,
            response_schema=schema,
        )
        response = self._client.models.generate_content(model=record.model, contents=prompt, config=config)

        usage = response.usage_metadata
        if usage is not None:
            thinking = usage.thoughts_token_count or 0
            record.input_tokens = usage.prompt_token_count or 0  # includes cached tokens
            record.cached_input_tokens = usage.cached_content_token_count or 0
            record.thinking_tokens = thinking
            record.output_tokens = (usage.candidates_token_count or 0) + thinking

        text = response.text or ""
        if schema is None:
            return text, None
        if isinstance(response.parsed, schema):
            return text, response.parsed
        finish = str(getattr(response.candidates[0], "finish_reason", "")) if response.candidates else ""
        return text, self._parse(record, schema, text, truncated="MAX_TOKENS" in finish)

    def _openai_compat(self, record, prompt, system, schema, temperature, max_output_tokens):
        import openai

        if self._client is None:
            self._client = openai.OpenAI(
                base_url=os.environ["OPENAI_COMPAT_BASE_URL"],
                api_key=os.getenv("OPENAI_COMPAT_API_KEY", "none"),
                timeout=180,
            )
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if schema:
            prompt = f"{prompt}\n\nRespond with JSON only, matching this JSON schema:\n{schema.model_json_schema()}"
        messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {"model": record.model, "messages": messages}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_output_tokens is not None:
            kwargs["max_tokens"] = max_output_tokens
        if schema:
            kwargs["response_format"] = {"type": "json_object"}
        response = self._client.chat.completions.create(**kwargs)

        if response.usage is not None:
            record.input_tokens = response.usage.prompt_tokens or 0
            record.output_tokens = response.usage.completion_tokens or 0
            details = response.usage.prompt_tokens_details
            record.cached_input_tokens = (getattr(details, "cached_tokens", 0) or 0) if details else 0
        text = response.choices[0].message.content or ""
        if schema is None:
            return text, None
        truncated = response.choices[0].finish_reason == "length"
        return text, self._parse(record, schema, text, truncated)


def _is_retryable(exc: Exception) -> bool:
    status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in RETRYABLE_STATUS
    # Transport failures (e.g. the connection dropped while the machine slept) are always retried.
    return type(exc).__name__ in {
        "APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout", "ReadTimeout",
        "ReadError", "WriteError", "RemoteProtocolError", "ServerDisconnectedError", "TimeoutException",
    }
