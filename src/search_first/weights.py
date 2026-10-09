"""Ranking weights as server-start settings; an agent calling a tool cannot change them."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, fields
from typing import TypeVar


@dataclass(frozen=True)
class RankWeights:
    idea_match: float = 0.50
    query_match: float = 0.17
    github_rank: float = 0.08
    variant_agreement: float = 0.04
    freshness: float = 0.05
    stars: float = 0.16


@dataclass(frozen=True)
class OverallWeights:
    fit: float = 0.6
    maintenance: float = 0.3
    stars: float = 0.1


DEFAULT_RANK = RankWeights()
DEFAULT_OVERALL = OverallWeights()
W = TypeVar("W", RankWeights, OverallWeights)
NUMBER = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", re.ASCII)
MAX_WEIGHT = 1e6


def parse_weights(text: str | None, kind: type[W]) -> W:
    """Parse `name=value,name=value` over the defaults, then normalise to sum 1.

    Raises ValueError with a message naming the allowed names.
    """
    defaults = kind()
    values = asdict(defaults)
    seen: set[str] = set()
    allowed = ", ".join(values)
    for item in (text or "").split(","):
        if not item.strip():
            continue
        name, sep, raw = (part.strip() for part in item.partition("="))
        if not sep or name not in values:
            raise ValueError(
                f"unknown or malformed weight {item.strip()!r}; allowed names: {allowed}"
            )
        if name in seen:
            raise ValueError(f"weight {name} is given twice; allowed names: {allowed}")
        seen.add(name)
        if not NUMBER.fullmatch(raw):
            raise ValueError(
                f"weight {name} must be a plain number, got {raw!r}; allowed names: {allowed}"
            )
        value = float(raw)
        if not math.isfinite(value) or not 0 <= value <= MAX_WEIGHT:
            raise ValueError(
                f"weight {name} must be a number from 0 to {MAX_WEIGHT:g}, got {raw!r}; "
                f"allowed names: {allowed}"
            )
        values[name] = value + 0.0  # turns -0 into 0
    total = math.fsum(values.values())
    if total <= 0:
        raise ValueError(f"weights must not all be zero; allowed names: {allowed}")
    return kind(**{f.name: values[f.name] / total for f in fields(kind)})


def weights_dict(weights: RankWeights | OverallWeights) -> dict[str, float]:
    return {name: round(value, 4) for name, value in asdict(weights).items()}
