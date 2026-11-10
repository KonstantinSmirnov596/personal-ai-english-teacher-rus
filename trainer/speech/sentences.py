"""Incremental sentence splitting of a streamed LLM reply, and cleanup before synthesis.

A sentence is emitted only when the character after the terminator proves a new sentence has
started, so abbreviations ("Mr.", "p.m.", "e.g."), decimals ("3.5") and initials ("J. R.") do
not cut a sentence in the middle.
"""

from __future__ import annotations

import re

ABBREVIATIONS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "st",
        "vs",
        "etc",
        "e.g",
        "i.e",
        "a.m",
        "p.m",
        "u.s",
        "u.k",
        "no",
        "inc",
        "ltd",
        "co",
        "corp",
        "approx",
        "dept",
        "est",
        "min",
        "max",
        "fig",
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",
        "mon",
        "tue",
        "wed",
        "thu",
        "fri",
        "sat",
        "sun",
    }
)

_TERMINATORS = ".!?…"
_CLOSERS = "\"')]”’»"
_OPENERS = "\"'(“‘«"
_MIN_CHARS = 2


def _is_abbreviation(text: str, dot_index: int) -> bool:
    """True when the '.' at dot_index ends a known abbreviation or a single initial."""
    j = dot_index
    while j > 0 and (text[j - 1].isalpha() or text[j - 1] == "."):
        j -= 1
    token = text[j:dot_index].lower()
    if not token:
        return False
    if token in ABBREVIATIONS:
        return True
    # Single capital letter initial, e.g. "J. Smith".
    return len(token) == 1 and text[j].isupper()


class SentenceSplitter:
    def __init__(self) -> None:
        self._buf = ""

    def feed(self, fragment: str) -> list[str]:
        self._buf += fragment
        out: list[str] = []
        while True:
            cut = self._find_cut()
            if cut is None:
                break
            sentence = self._buf[:cut].strip()
            self._buf = self._buf[cut:].lstrip()
            if len(sentence) >= _MIN_CHARS:
                out.append(sentence)
        return out

    def flush(self) -> list[str]:
        rest = self._buf.strip()
        self._buf = ""
        return [rest] if len(rest) >= _MIN_CHARS else []

    def _find_cut(self) -> int | None:
        buf = self._buf
        i = 0
        n = len(buf)
        while i < n:
            ch = buf[i]
            if ch == "\n":
                if buf[:i].strip():
                    return i + 1
                i += 1
                continue
            if ch in _TERMINATORS:
                end = i + 1
                while end < n and (buf[end] in _TERMINATORS or buf[end] in _CLOSERS):
                    end += 1
                if end >= n:
                    return None  # need the next character to decide
                if not buf[end].isspace():
                    i = end  # "3.5", "e.g." inside a token, "U.S." etc.
                    continue
                k = end
                while k < n and buf[k].isspace():
                    k += 1
                if k >= n:
                    return None
                nxt = buf[k]
                starts_sentence = nxt.isupper() or nxt.isdigit() or nxt in _OPENERS
                if ch == "." and _is_abbreviation(buf, i):
                    starts_sentence = False
                if starts_sentence:
                    return end
                i = end
                continue
            i += 1
        return None


_MARKUP = re.compile(r"[*#_`~]+")
_BRACKETS = re.compile(r"\[[^\]]*\]")
_SPACES = re.compile(r"\s+")
_CYRILLIC = re.compile(r"[Ѐ-ԯ]")


def has_cyrillic(text: str) -> bool:
    return bool(_CYRILLIC.search(text))


def clean_for_speech(text: str) -> str:
    """Remove markup characters and bracketed content before synthesis."""
    text = _BRACKETS.sub(" ", text)
    text = _MARKUP.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def clean_for_display(text: str) -> str:
    """The feed shows the reply without markup or [asr_unsure: ...] echoes."""
    text = _BRACKETS.sub("", text)
    text = _MARKUP.sub("", text)
    return re.sub(r"[ \t]+", " ", text).strip()
