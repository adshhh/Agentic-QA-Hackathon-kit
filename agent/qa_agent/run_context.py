"""Per-run artifact directory: runs/<timestamp>/ with screenshots/ and JSON outputs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class RunContext:
    root: Path

    @classmethod
    def create(cls, runs_dir: str | Path) -> RunContext:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        root = Path(runs_dir) / stamp
        (root / "screenshots").mkdir(parents=True, exist_ok=False)
        return cls(root=root)

    @property
    def screenshots(self) -> Path:
        return self.root / "screenshots"

    def path(self, name: str) -> Path:
        return self.root / name

    def write_json(self, name: str, data: Any) -> Path:
        target = self.path(name)
        target.write_text(json.dumps(data, indent=2, default=str) + "\n")
        return target

    def append_jsonl(self, name: str, record: dict[str, Any]) -> None:
        with self.path(name).open("a") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
