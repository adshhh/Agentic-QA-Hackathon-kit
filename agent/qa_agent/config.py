"""Runtime configuration: target URLs, model per phase, and model pricing.

Everything is overridable via environment variables (loaded from agent/.env by direnv).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Phases that call a model. Each maps to a model name in Settings.models (DECISIONS.md D13).
PHASES = ("interpret", "explore", "plan", "critique", "recover", "revise", "review", "judge", "report", "ping")

# USD per 1M tokens, standard paid tier. Source: ai.google.dev/gemini-api/docs/pricing.
PRICING_DATE = "2026-10-21"


@dataclass(frozen=True)
class Price:
    input: float
    output: float  # Gemini bills thinking tokens at the output rate
    cached_input: float


PRICES: dict[str, Price] = {
    "gemini-3.8-flash": Price(input=0.75, output=3.75, cached_input=0.075),
    "gemini-3.5-flash-lite": Price(input=0.30, output=2.50, cached_input=0.03),
}
FREE = Price(0.0, 0.0, 0.0)  # e.g. a local Ollama model behind an OpenAI-compatible endpoint


def _default_models() -> dict[str, str]:
    thinking = os.getenv("QA_MODEL_THINKING", "gemini-3.8-flash")
    light = os.getenv("QA_MODEL_LIGHT", "gemini-3.5-flash-lite")
    models = {phase: thinking for phase in PHASES}
    models["recover"] = light
    return models


@dataclass
class Settings:
    target_url: str = field(default_factory=lambda: os.getenv("TARGET_URL", "http://localhost:5173"))
    api_url: str = field(default_factory=lambda: os.getenv("API_URL", "http://localhost:8000"))
    database_url: str | None = field(default_factory=lambda: os.getenv("DEMOBANK_DATABASE_URL") or None)
    # "gemini" (default) or "openai_compat" (any OpenAI-compatible endpoint, DECISIONS.md D15)
    provider: str = field(default_factory=lambda: os.getenv("QA_PROVIDER", "gemini"))
    models: dict[str, str] = field(default_factory=_default_models)
    runs_dir: str = field(default_factory=lambda: os.getenv("QA_RUNS_DIR", "runs"))

    def model_for(self, phase: str) -> str:
        if phase not in self.models:
            raise ValueError(f"Unknown phase {phase!r}; expected one of {PHASES}")
        return self.models[phase]


def price_for(model: str) -> Price:
    return PRICES.get(model, FREE)
