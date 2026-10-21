"""Self-evaluation helpers: INTERPRET recall against the hand-made PRD inventory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .interpret import _normalize
from .schemas import Requirement

INVENTORY = Path(__file__).resolve().parent.parent / "eval" / "prd_inventory.json"


def interpret_recall(requirements: list[Requirement], inventory_path: Path = INVENTORY) -> dict[str, Any]:
    items = json.loads(inventory_path.read_text())["items"]
    quotes = [_normalize(r.prd_quote) for r in requirements]
    covered, missed = [], []
    for item in items:
        phrase = _normalize(item["phrase"])
        (covered if any(phrase in q for q in quotes) else missed).append(item["id"])
    return {
        "inventory_size": len(items),
        "covered": len(covered),
        "recall": round(len(covered) / len(items), 3),
        "missed": missed,
    }
