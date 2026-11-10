"""Access to the bundled data files in ``trainer/data``."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"


@cache
def load_json(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def scenarios() -> list[dict[str, Any]]:
    return list(load_json("scenarios.json")["scenarios"])


def patterns() -> list[dict[str, Any]]:
    return list(load_json("patterns.json")["patterns"])


def pattern_keys() -> list[str]:
    return [p["key"] for p in patterns()]


def pattern_by_key() -> dict[str, dict[str, Any]]:
    return {p["key"]: p for p in patterns()}


def reading_phrases() -> dict[str, Any]:
    return dict(load_json("reading_phrases.json"))


def l1_catalog() -> dict[str, Any]:
    return dict(load_json("l1_ru_catalog.json"))


def corrector_eval() -> list[dict[str, Any]]:
    lines = (DATA_DIR / "corrector_eval.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]
