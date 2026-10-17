"""Configurable weights. Read from `[tool.cdebt]` in pyproject.toml or `.cdebt/config.toml`."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class Config(BaseModel):
    w_churn: float = 1.0  # weight on normalised churn in (1 + w_churn * churn)
    churn_days: int = 90
    blast_mode: Literal["log", "raw"] = "log"  # log: 1+log1p(refs); raw: max(1, refs)
    unknown_as_ai: bool = False  # worst-case view: treat unknown lines as AI
    use_trailers: bool = True
    extensions: tuple[str, ...] = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
    reading_lines_per_min: float = 30.0  # plan effort heuristic: a GUESS, not measured
    unit_overhead_min: float = 2.0  # plan effort heuristic: a GUESS, not measured


def load_config(repo: Path) -> Config:
    for f, key in ((repo / ".cdebt" / "config.toml", None), (repo / "pyproject.toml", "cdebt")):
        if f.exists():
            data = tomllib.loads(f.read_text())
            if key:
                data = data.get("tool", {}).get(key, {})
            if data:
                return Config.model_validate(data)
    return Config()
