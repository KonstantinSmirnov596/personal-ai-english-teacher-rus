"""Session summary (stage 2.4 adds the LLM key takeaway and exercise)."""

from __future__ import annotations

from typing import Any


async def build_summary(core: Any, session: Any) -> dict[str, Any]:
    corrections = [
        {
            "id": c["id"],
            "turn_id": c["turn_id"],
            "quote": c["quote"],
            "fix": c["fix"],
            "type": c["type"],
            "note_ru": c["note_ru"],
            "pattern": c["pattern"],
        }
        for c in core.sessions.corrections(session.id)
    ]
    return {
        "key_takeaway": None,
        "exercise": None,
        "corrections": corrections,
        "patterns": [],
        "sounds": [],
        "phrases": [],
    }
