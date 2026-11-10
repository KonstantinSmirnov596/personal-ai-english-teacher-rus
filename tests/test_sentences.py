from __future__ import annotations

import pytest

from trainer.speech.sentences import (
    SentenceSplitter,
    clean_for_speech,
    has_cyrillic,
)


def split_stream(text: str, chunk: int = 3) -> list[str]:
    s = SentenceSplitter()
    out: list[str] = []
    for i in range(0, len(text), chunk):
        out.extend(s.feed(text[i : i + chunk]))
    out.extend(s.flush())
    return out


@pytest.mark.parametrize("chunk", [1, 2, 5, 1000])
def test_basic_split(chunk: int) -> None:
    text = "That sounds great! What did you do next? I went home."
    assert split_stream(text, chunk) == [
        "That sounds great!",
        "What did you do next?",
        "I went home.",
    ]


def test_abbreviations_do_not_split() -> None:
    text = "I met Mr. Smith at 5 p.m. yesterday. Dr. Brown was there too."
    assert split_stream(text) == [
        "I met Mr. Smith at 5 p.m. yesterday.",
        "Dr. Brown was there too.",
    ]


def test_numbers_do_not_split() -> None:
    text = "It costs 3.50 dollars, or about 3.5 percent. Version 2.0 is out."
    assert split_stream(text) == [
        "It costs 3.50 dollars, or about 3.5 percent.",
        "Version 2.0 is out.",
    ]


def test_eg_and_us() -> None:
    text = "Some tools, e.g. Jira, help. We moved to the U.S. in May. Nice!"
    assert split_stream(text) == [
        "Some tools, e.g. Jira, help.",
        "We moved to the U.S. in May.",
        "Nice!",
    ]


def test_initials() -> None:
    assert split_stream("J. R. Tolkien wrote it. Have you read it?") == [
        "J. R. Tolkien wrote it.",
        "Have you read it?",
    ]


def test_quotes_and_ellipsis() -> None:
    text = 'She said "Sure." Then she left... Really? Yes.'
    assert split_stream(text) == ['She said "Sure."', "Then she left...", "Really?", "Yes."]


def test_lowercase_after_period_keeps_sentence() -> None:
    assert split_stream("The meeting is at 10 a.m. on Monday.") == [
        "The meeting is at 10 a.m. on Monday."
    ]


def test_sentence_waits_for_next_char() -> None:
    s = SentenceSplitter()
    assert s.feed("Hello there.") == []
    assert s.feed(" How") == ["Hello there."]
    assert s.flush() == ["How"]


def test_newline_is_boundary() -> None:
    assert split_stream("First line\nSecond line") == ["First line", "Second line"]


def test_clean_for_speech() -> None:
    assert clean_for_speech("**Great** idea [asr_unsure: foo] `x`") == "Great idea x"


def test_has_cyrillic() -> None:
    assert has_cyrillic("Привет")
    assert not has_cyrillic("Hello, world")
