"""Pronunciation logic without the acoustic model: reference, alignment, catalog, levels."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tests.helpers import make_app
from trainer.pron import catalog
from trainer.pron.align import forced_align, needleman_wunsch
from trainer.pron.assess import DEFAULT_THRESHOLDS as TH
from trainer.pron.assess import build_result, judge
from trainer.pron.engine import Evidence, PhoneEvidence
from trainer.pron.reference import RefPhone, build_reference


def phones_of(ref, word: str) -> list[RefPhone]:
    w = next(w for w in ref.words if w.text.lower() == word)
    return [ref.phones[i] for i in w.phones]


def test_reference_words_and_merged_units() -> None:
    text = "I think we should work on the third version this week."
    ref = build_reference(text.split())
    assert [w.text for w in ref.words] == [w.strip(".") for w in text.split()]
    # espeak prints "on the" as one unit; it is split back into two words
    assert [p.symbol for p in phones_of(ref, "on")] == ["ɔ", "n"]
    assert [p.symbol for p in phones_of(ref, "the")] == ["ð", "ə"]
    assert [p.symbol for p in phones_of(ref, "think")] == ["θ", "ɪ", "ŋ", "k"]
    think = phones_of(ref, "think")
    assert think[1].stressed and think[0].word_initial and think[-1].word_final
    assert all(i == ref.words[p.word].phones[p.index_in_word] for i, p in enumerate(ref.phones))


def test_reference_hyphen_and_source_index() -> None:
    ref = build_reference(["Thirty-three", "people", "joined."])
    assert [(w.text, w.source_index) for w in ref.words] == [
        ("Thirty", 0),
        ("three", 0),
        ("people", 1),
        ("joined", 2),
    ]


def test_reference_numbers_stay_in_one_word() -> None:
    ref = build_reference(["We", "shipped", "25", "builds."])
    assert [w.text for w in ref.words] == ["We", "shipped", "25", "builds"]
    assert len(ref.words[2].phones) >= 6  # "twenty five"


def test_american_tolerances() -> None:
    ref = build_reference(["The", "water", "was", "not", "hot."])
    flap = next(p for p in phones_of(ref, "water") if p.cls == "ɾ")
    assert ("t",) in flap.accept and ("d",) in flap.accept
    final_t = phones_of(ref, "hot")[-1]
    assert ("ʔ",) in final_t.accept and () in final_t.accept
    vowel = phones_of(ref, "hot")[1]
    assert vowel.cls == "ɑː" and ("ɔː",) in vowel.accept
    assert all(v != ("ɔː",) for v in vowel.competitors)


def test_function_words_are_lenient() -> None:
    ref = build_reference(["I", "want", "to", "talk", "to", "the", "team."])
    to = phones_of(ref, "to")
    assert not to[1].scored and to[0].deletion_ok
    the = phones_of(ref, "the")
    assert the[0].scored and the[0].cls == "ð"  # th in "the" is still checked
    team = phones_of(ref, "team")
    assert all(p.scored for p in team)


def test_r_colored_vowel_is_one_position() -> None:
    ref = build_reference(["car"])
    ar = phones_of(ref, "car")[-1]
    assert ar.cls == "ɑːɹ"
    assert ("ɑːɹ",) in ar.accept and ("ɑː", "ɹ") in ar.accept
    assert ("ɑː",) in ar.competitors  # r dropped
    ref2 = build_reference(["there"])
    assert phones_of(ref2, "there")[-1].cls == "ɛɹ"


def test_word_final_ng_has_sequence_competitors() -> None:
    ref = build_reference(["sing"])
    ng = phones_of(ref, "sing")[-1]
    assert ("ŋ", "k") in ng.competitors and ("n",) in ng.competitors


def test_forced_align_on_synthetic_posteriors() -> None:
    # 3 classes + blank; frames: blank, A, A, blank, B, blank, C, C
    seq = [0, 1, 1, 0, 2, 0, 3, 3]
    lp = np.full((len(seq), 4), np.log(0.02))
    for t, c in enumerate(seq):
        lp[t, c] = np.log(0.94)
    assert forced_align(lp, [1, 2, 3]) == [(1, 2), (4, 4), (6, 7)]
    with pytest.raises(ValueError):
        forced_align(lp[:2], [1, 2, 3])
    # a repeated symbol needs a blank between its two occurrences
    lp2 = np.full((5, 2), np.log(0.1))
    for t, c in enumerate([1, 1, 0, 1, 1]):
        lp2[t, c] = np.log(0.9)
    assert forced_align(lp2, [1, 1]) == [(0, 1), (3, 4)]


def test_needleman_wunsch() -> None:
    pairs = needleman_wunsch(list("abcd"), list("abxd"))
    assert pairs == [(0, 0), (1, 1), (2, 2), (3, 3)]
    pairs = needleman_wunsch(list("abc"), list("ac"))
    assert (1, None) in pairs and len(pairs) == 3
    pairs = needleman_wunsch(list("ac"), list("abc"))
    assert (None, 1) in pairs


def test_catalog_matching() -> None:
    ref = build_reference(["I", "think", "the", "bad", "dog", "can", "sing."])
    theta = phones_of(ref, "think")[0]
    assert catalog.match(theta, ("s",)).id == "th_voiceless"
    assert catalog.match(theta, ("p",)) is None
    bad = phones_of(ref, "bad")
    assert catalog.match(bad[-1], ("t",)).id == "final_devoicing"
    assert catalog.match(bad[0], ("p",)) is None  # only word-final devoicing is catalogued
    assert catalog.match(bad[1], ("ɛ",)).id == "ae_to_eh"
    ng = phones_of(ref, "sing")[-1]
    assert catalog.match(ng, ("n",)).id == "ng_to_n"
    assert catalog.match(ng, ("ŋ", "k")).id == "ng_plus_stop"
    the = phones_of(ref, "the")[0]
    assert catalog.match(the, ("z",)).id == "th_voiced"
    car = phones_of(build_reference(["car"]), "car")[-1]
    assert catalog.match(car, ("ɑː",)).id == "r_dropped"
    assert catalog.match(car, ("ɑː", "r")).id == "r_trill"
    word = phones_of(build_reference(["word"]), "word")[1]
    assert catalog.match(word, ("ɔːɹ",)).id == "nurse_vowel"
    tip = catalog.tip(catalog.match(theta, ("s",)), "s")
    assert tip["pair"] == ["think", "sink"] and tip["tip_ru"]


def ev(llr: dict) -> PhoneEvidence:
    return PhoneEvidence(start=0.0, end=0.1, accept_logp=-1.0, best_accept=(), llr=llr)


def test_levels_and_thresholds() -> None:
    ref = build_reference(["I", "think", "the", "bad", "dog", "can", "sing."])
    theta = phones_of(ref, "think")[0]
    assert judge(theta, ev({("s",): TH.cat_error + 0.1})).level == "error"
    assert judge(theta, ev({("s",): TH.cat_error - 0.1})).level == "approx"
    assert judge(theta, ev({("s",): TH.cat_approx - 0.1})).level == "ok"
    # the owner's «Это было верно» raises the margin for this sound
    assert judge(theta, ev({("s",): TH.cat_error + 0.1}), offset=0.75).level == "approx"
    # conversation: strict margin, catalogued errors only
    assert judge(theta, ev({("s",): TH.cat_error + 0.1}), strict=True).level == "ok"
    strict = judge(theta, ev({("s",): TH.cat_error + TH.strict_margin + 0.1}), strict=True)
    assert strict.level == "error" and strict.heard == ("s",) and strict.entry.id == "th_voiceless"
    # an uncatalogued substitution needs much more evidence and is never strict
    assert judge(theta, ev({("p",): TH.gen_approx + 0.1})).level == "approx"
    assert judge(theta, ev({("p",): TH.gen_error + 0.1})).level == "error"
    assert judge(theta, ev({("p",): TH.gen_error + 5})).gop < -TH.gen_error
    assert judge(theta, ev({("p",): TH.gen_error + 5}), strict=True).level == "ok"
    # a missing sound is at most «неточно»
    assert judge(theta, ev({(): 50.0})).level == "approx"
    # a catalogued competitor wins over a slightly stronger uncatalogued one
    j = judge(theta, ev({("s",): 2.5, ("p",): 3.0}))
    assert j.level == "error" and j.heard == ("s",)
    # entries whose default level is «неточно» never become errors
    ng = phones_of(ref, "sing")[-1]
    assert judge(ng, ev({("ŋ", "k"): 20.0})).level == "approx"
    # negative margins: said as expected
    ok = judge(theta, ev({("s",): -4.0, ("f",): -2.0}))
    assert ok.level == "ok" and ok.heard is None and ok.gop == 0.0


def test_build_result_shape_and_scores() -> None:
    source = ["I", "think", "so."]
    ref = build_reference(source)
    phones = []
    for p in ref.phones:
        llr = {c: -5.0 for c in p.competitors}
        if p.cls == "θ":
            llr[("s",)] = 6.0
        phones.append(ev(llr))
    res = build_result(ref, Evidence(phones, [], 1.0, 1.0), source)
    words = {w["w"]: w for w in res["words"]}
    assert list(words) == ["I", "think", "so"]
    think = words["think"]
    assert think["level"] == "error" and think["ipa"] == "θɪŋk"
    assert think["phones"][0] == {
        "expected": "θ",
        "cls": "θ",
        "heard": "s",
        "start": 0.0,
        "end": 0.1,
        "gop": -6.0,
        "level": "error",
        "scored": True,
        "catalog_id": "th_voiceless",
    }
    assert think["tip"]["catalog_id"] == "th_voiceless" and think["tip"]["phone_index"] == 0
    assert think["tip"]["pair"] == ["think", "sink"]
    assert words["so"]["level"] == "ok" and words["so"]["score"] == 1.0
    assert 0.0 < think["score"] < 1.0
    assert res["score"] == pytest.approx((1.0 + think["score"] + 1.0) / 3, abs=1e-3)


def test_pron_routes_without_model(tmp_path: Path) -> None:
    with make_app(tmp_path) as ctx:
        phrases = ctx.client.get("/api/pron/phrases").json()
        assert len(phrases) == 26 and phrases[0]["id"] == "p01"
        assert phrases[0]["targets"][0]["title_ru"]
        h = ctx.auth_headers
        r = ctx.client.post("/api/pron/assess?phrase_id=p01", content=b"not a wav", headers=h)
        assert r.status_code == 422 and r.json()["error"]["code"] == "pron_bad_audio"
        r = ctx.client.post("/api/pron/assess?phrase_id=nope", content=b"x", headers=h)
        assert r.status_code == 404
        r = ctx.client.post(
            "/api/pron/feedback",
            json={"attempt_id": "x", "word_index": 0, "phone_index": 0, "verdict": "was_correct"},
            headers=h,
        )
        assert r.status_code == 404
        assert ctx.client.get("/api/pron/sounds?days=7").json() == []
        assert ctx.client.get("/api/pron/reference?text=Привет").status_code == 422
