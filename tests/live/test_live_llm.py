"""Live provider checks with the owner's active connection (costs money, at most 80 requests).

Reads the owner's database read-only and the key from the real keyring service read-only; the
key is never printed. Skipped when no connection with a saved key exists.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import keyring
import pytest

from tests.conftest import REAL_DATA_DIR
from trainer.corrector import run_corrector
from trainer.data_files import corrector_eval
from trainer.llm.client import Endpoint, LLMClient
from trainer.logging_setup import register_secret
from trainer.prompts import tutor as tutor_prompt

pytestmark = pytest.mark.live

REPORT = Path(__file__).resolve().parents[2] / "docs" / "reports" / "data" / "live_stage1.json"
CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def owner_endpoint() -> Endpoint | None:
    db = REAL_DATA_DIR / "trainer.db"
    if not db.exists():
        return None
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM connection WHERE is_active = 1").fetchone()
    if row is None:
        return None
    key = keyring.get_password("EnglishTrainer", f"connection-{row['id']}")
    if not key:
        return None
    register_secret(key)
    return Endpoint(
        base_url=row["base_url"],
        model=row["model"],
        api_key=key,
        extra_params=json.loads(row["extra_params"] or "{}"),
        json_mode=bool(row["json_mode"]),
    )


@pytest.fixture(scope="module")
def endpoint() -> Endpoint:
    ep = owner_endpoint()
    if ep is None:
        pytest.skip("no active connection with a saved key (docs/HUMAN_TASKS.md, stage 1)")
    return ep


def _save(section: str, data: dict[str, Any]) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    current = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
    current[section] = data
    REPORT.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")


def test_live_stream_first_chunk(endpoint: Endpoint) -> None:
    from trainer.data_files import scenarios

    system = tutor_prompt.build_system_prompt(
        level="B1", scenario=scenarios()[0], recast=True, focus_lines=[]
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "assistant", "content": "Hey, good to see you. What's been on your mind lately?"},
        {"role": "user", "content": "I was thinking about my vacation plans for next summer."},
    ]

    async def run() -> tuple[float, float, str]:
        client = LLMClient()
        started = time.perf_counter()
        first: float | None = None
        parts: list[str] = []
        async for part in client.stream_chat(endpoint, messages, max_tokens=200):
            if first is None:
                first = time.perf_counter() - started
            parts.append(part)
        total = time.perf_counter() - started
        await client.aclose()
        return first or total, total, "".join(parts)

    first, total, text = asyncio.run(run())
    _save(
        "stream", {"first_chunk_s": round(first, 3), "total_s": round(total, 3), "chars": len(text)}
    )
    assert text.strip()
    assert not CYRILLIC.search(text)


def test_live_corrector_quality(endpoint: Endpoint) -> None:
    cases = corrector_eval()
    assert len(cases) * 2 + 2 <= 80  # request budget of the live suite

    async def run() -> list[dict[str, Any]]:
        client = LLMClient()
        out: list[dict[str, Any]] = []
        for case in cases:
            res = await run_corrector(
                client,
                endpoint,
                level=case.get("level", "B1"),
                tutor_said=case.get("tutor_said", ""),
                learner_said=case["learner_said"],
                unsure_words=case.get("asr_unsure", []),
            )
            out.append({"case": case, "status": res.status, "items": res.items})
        await client.aclose()
        return out

    results = asyncio.run(run())
    schema_ok = sum(r["status"] != "failed" for r in results) / len(results)
    items = [(r, i) for r in results for i in r["items"]]
    quote_ok = sum(i["start"] is not None for _r, i in items) / max(1, len(items))
    clean = [r for r in results if r["case"]["expect"] == []]
    clean_ok = sum(not r["items"] for r in clean) / max(1, len(clean))
    with_err = [r for r in results if r["case"]["expect"]]
    pattern_ok = sum(
        any(i["pattern"] in r["case"]["expect"] for i in r["items"]) for r in with_err
    ) / max(1, len(with_err))
    forbid_ok = all(
        not any(
            w.lower() in i["quote"].lower().split()
            for w in r["case"].get("forbid_quote", []) + r["case"].get("asr_unsure", [])
            for i in r["items"]
        )
        for r in results
    )
    note_ok = all(
        CYRILLIC.search(i["note_ru"]) and len(i["note_ru"].split()) <= 25 for _r, i in items
    )
    metrics = {
        "schema_ok": round(schema_ok, 3),
        "quote_substring": round(quote_ok, 3),
        "clean_without_items": round(clean_ok, 3),
        "expected_pattern_found": round(pattern_ok, 3),
        "low_conf_never_quoted": forbid_ok,
        "note_ru_cyrillic_and_short": note_ok,
        "cases": len(results),
        "items": len(items),
    }
    _save("corrector", metrics)
    assert schema_ok == 1.0
    assert quote_ok >= 0.95
    assert clean_ok >= 0.80
    assert pattern_ok >= 0.70
    assert forbid_ok
    assert note_ok
