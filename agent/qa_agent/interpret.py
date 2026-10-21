"""INTERPRET: PRD -> atomic, checkable requirements (one model call, then code validation).

Code validates what the model cannot be trusted with: every prd_quote must appear verbatim in
the PRD (no invented requirements), ids must be unique, and test depth comes from the area
(DECISIONS.md D17), not from the model.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from .llm import LLM
from .schemas import Requirement, RequirementSet

DEEP_AREAS = {"access", "transfers", "history"}  # D17

SYSTEM = """You are a senior QA analyst. You turn a product requirements document (PRD) into
atomic, independently testable requirements for a black-box tester who can operate the web UI
and read its HTTP API responses.

Method (least-to-most decomposition):
1. Read the PRD section by section.
2. Split every compound sentence into atomic behaviors. "Blank, zero, negative, non-numeric and
   over-precision values must be rejected" is ONE requirement about amount validation, but
   "must not alter balances or create a transaction" adds observable side effects to it.
3. For each behavior list the observable outcomes a tester must check, INCLUDING side effects
   that must or must not happen (balances, transaction records in each account history,
   redirects, error/success messages, what is visible in the DOM and API responses).
4. Mark opinion-based wording ("clear", "clearly labeled", "readable", "usable") in
   subjective_aspect; keep the objective part testable.
5. Seed-data expectations and general UI behavior are requirements too.

Rules:
- prd_quote must be copied VERBATIM from the PRD (a full sentence or a contiguous fragment).
- Do not invent behavior the PRD does not state. Skip the "Out of scope" section.
- Ids: <AREA>-<NN> using ACC (access/session), DSH (accounts/dashboard), TRF (transfers),
  TXN (history), PRO (profile), GEN (general UI), SEED (seed data). Area values:
  access, accounts, transfers, history, profile, general, seed.
- Aim for completeness: every testable statement in the PRD must be covered by some requirement.
"""


def _normalize(text: str) -> str:
    text = text.replace("**", "").replace("`", "")
    text = text.replace("×", "x").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def _fragments(quote: str) -> list[str]:
    """Split a quote into sentences / '...'-separated fragments; each must be verbatim PRD text."""
    parts = re.split(r"\.\.\.|…|(?<=[.!?])\s+", quote)
    return [p for p in (_normalize(x).strip(" .") for x in parts) if p]


def quote_in_prd(quote: str, prd: str) -> bool:
    """True if every sentence/fragment of the quote appears verbatim in the PRD."""
    norm_prd = _normalize(prd)
    fragments = _fragments(quote)
    return bool(fragments) and all(f in norm_prd for f in fragments)


def prd_sentences(prd: str) -> list[str]:
    lines = [re.sub(r"^\s*(?:[-*]|#+)\s*", "", ln) for ln in prd.splitlines()]
    sentences: list[str] = []
    for ln in lines:
        sentences += [s.strip() for s in re.split(r"(?<=[.!?])\s+", ln) if len(s.strip()) > 15]
    return sentences


def repair_quote(quote: str, prd: str, threshold: float = 0.85) -> str | None:
    """Replace a near-verbatim quote with the closest real PRD sentence(s), or None."""
    sentences = prd_sentences(prd)
    repaired: list[str] = []
    for frag in _fragments(quote):
        if frag in _normalize(prd):
            repaired.append(next((s for s in sentences if frag in _normalize(s)), frag))
            continue
        best = max(sentences, key=lambda s: difflib.SequenceMatcher(None, frag, _normalize(s)).ratio())
        if difflib.SequenceMatcher(None, frag, _normalize(best)).ratio() < threshold:
            return None
        repaired.append(best)
    return " ".join(dict.fromkeys(repaired)) or None


def interpret(llm: LLM, prd_text: str) -> tuple[list[Requirement], dict[str, Any]]:
    result = llm.generate(
        "interpret",
        f"PRD:\n<prd>\n{prd_text}\n</prd>\n\nExtract the requirements.",
        system=SYSTEM,
        schema=RequirementSet,
    )
    requirements: list[Requirement] = result.parsed.requirements

    issues: list[str] = []
    seen: set[str] = set()
    kept: list[Requirement] = []
    for req in requirements:
        if req.id in seen:
            req.id = f"{req.id}-{len(seen)}"
            issues.append(f"duplicate id renamed to {req.id}")
        seen.add(req.id)
        if not quote_in_prd(req.prd_quote, prd_text):
            fixed = repair_quote(req.prd_quote, prd_text)
            if fixed is None:
                issues.append(f"{req.id}: prd_quote not found in PRD; dropped. Quote was: {req.prd_quote!r}")
                continue
            issues.append(f"{req.id}: prd_quote repaired to the closest verbatim PRD sentence(s)")
            req.prd_quote = fixed
        kept.append(req)

    validation = {"extracted": len(requirements), "kept": len(kept), "issues": issues}
    return kept, validation


def depth_for(req: Requirement) -> str:
    return "deep" if req.area in DEEP_AREAS else "light"
