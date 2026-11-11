"""Acoustic evidence for each reference phone (plan items 3.2–3.5).

For every reference position the engine compares, on a window of frames around the phone, the
CTC likelihood of the accepted pronunciations with the likelihood of each competitor (another
phone in its place, a deletion, or a catalog sequence such as ŋ+k):

    LLR(competitor) = log P(window | reference with competitor) - log P(window | reference)

This is Goodness of Pronunciation for a CTC model: GOP = -max(0, max LLR). The competitor with
the highest LLR is the diagnosis of what was said instead. The window comes from a Viterbi
forced alignment of the reference; a free phone recognition aligned to the reference with
Needleman–Wunsch gives a second, independent diagnosis.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from trainer.pron.align import forced_align, needleman_wunsch
from trainer.pron.inventory import BLANK, Inventory, build_inventory
from trainer.pron.phoneme_model import PhonemeModel
from trainer.pron.reference import Reference, Variant
from trainer.speech.audio import FloatAudio

FRAME_S = 0.02
CONTEXT = 2  # reference positions on each side of the scored phone
PAD_FRAMES = 15
BATCH = 4096


class AlignmentError(Exception):
    """The recording cannot be aligned to the reference (too short or empty)."""


@dataclass
class PhoneEvidence:
    start: float
    end: float
    accept_logp: float
    best_accept: Variant
    llr: dict[Variant, float] = field(default_factory=dict)
    free: Variant | None = None  # what free recognition heard at this position

    def best(self) -> tuple[Variant | None, float]:
        if not self.llr:
            return None, float("-inf")
        comp = max(self.llr, key=lambda v: self.llr[v])
        return comp, self.llr[comp]


@dataclass
class Evidence:
    phones: list[PhoneEvidence]
    free_decode: list[str]
    duration_s: float
    speech_ratio: float  # recognized phones / reference phones


class PronEngine:
    def __init__(self, model: PhonemeModel) -> None:
        self.model = model
        self._inventory: Inventory | None = None

    @property
    def inventory(self) -> Inventory:
        if self._inventory is None:
            self.model.load()
            self._inventory = build_inventory(self.model.vocab, self.model.blank_id)
        return self._inventory

    def class_log_probs(self, audio_16k: FloatAudio) -> np.ndarray:
        """(frames, classes) log-probabilities; column 0 is the CTC blank."""
        inv = self.inventory
        probs = np.exp(self.model.log_probs(audio_16k).astype(np.float64))
        reduced = probs @ inv.membership.astype(np.float64)
        return np.log(np.maximum(reduced, 1e-12)).astype(np.float32)

    def free_decode(self, class_lp: np.ndarray) -> list[tuple[str, int]]:
        ids = class_lp.argmax(axis=1)
        out: list[tuple[str, int]] = []
        prev = BLANK
        for t, c in enumerate(ids):
            if c != prev and c != BLANK:
                out.append((self.inventory.names[int(c)], t))
            prev = int(c)
        return out

    def analyze(self, audio_16k: FloatAudio, ref: Reference) -> Evidence:
        inv = self.inventory
        class_lp = self.class_log_probs(audio_16k)
        n_frames = class_lp.shape[0]
        phones = ref.phones
        if not phones:
            return Evidence([], [], len(audio_16k) / 16000.0, 0.0)

        # --- forced alignment of the canonical reference ---------------------------------
        targets: list[int] = []
        owner: list[list[int]] = []  # position -> indices into targets
        for p in phones:
            owner.append(list(range(len(targets), len(targets) + len(p.canonical))))
            targets.extend(inv.cls(c) for c in p.canonical)
        try:
            spans = forced_align(class_lp, targets, BLANK)
        except ValueError as e:
            raise AlignmentError(str(e)) from e
        first = [spans[o[0]][0] for o in owner]
        last = [spans[o[-1]][1] for o in owner]

        # --- windowed CTC likelihood of every accepted variant and competitor -------------
        items: list[tuple[int, int, list[int]]] = []
        index: list[tuple[int, Variant, bool]] = []  # (position, variant, is_accept)
        for i, p in enumerate(phones):
            if not p.scored and len(p.accept) <= 1:
                continue
            lo_pos, hi_pos = max(0, i - CONTEXT), min(len(phones) - 1, i + CONTEXT)
            lo = last[lo_pos - 1] + 1 if lo_pos > 0 else 0
            hi = first[hi_pos + 1] - 1 if hi_pos + 1 < len(phones) else n_frames - 1
            lo = max(lo, first[lo_pos] - PAD_FRAMES)
            hi = min(hi, last[hi_pos] + PAD_FRAMES)
            left = [inv.cls(c) for q in phones[lo_pos:i] for c in q.canonical]
            right = [inv.cls(c) for q in phones[i + 1 : hi_pos + 1] for c in q.canonical]
            for variant in p.accept:
                items.append((lo, hi, left + [inv.cls(c) for c in variant] + right))
                index.append((i, variant, True))
            for variant in p.competitors if p.scored else []:
                items.append((lo, hi, left + [inv.cls(c) for c in variant] + right))
                index.append((i, variant, False))
        logp = self._ctc_logp(class_lp, items)

        free = self.free_decode(class_lp)
        free_names = [name for name, _ in free]
        canon_names = [c for p in phones for c in p.canonical]
        pairs = needleman_wunsch(canon_names, free_names)
        heard_at: dict[int, list[str]] = {}
        target_owner = [pos for pos, o in enumerate(owner) for _ in o]
        last_pos: int | None = None
        for ri, hi_idx in pairs:
            if ri is not None:
                last_pos = target_owner[ri]
                if hi_idx is not None:
                    heard_at.setdefault(last_pos, []).append(free_names[hi_idx])
                else:
                    heard_at.setdefault(last_pos, [])
            elif last_pos is not None and hi_idx is not None:
                heard_at.setdefault(last_pos, []).append(free_names[hi_idx])  # insertion

        out: list[PhoneEvidence] = []
        for i in range(len(phones)):
            end = first[i + 1] if i + 1 < len(phones) else last[i] + 1
            end = min(end, last[i] + 1 + PAD_FRAMES)
            out.append(
                PhoneEvidence(
                    start=round(first[i] * FRAME_S, 3),
                    end=round(max(end, first[i] + 1) * FRAME_S, 3),
                    accept_logp=float("-inf"),
                    best_accept=phones[i].canonical,
                    free=tuple(heard_at[i]) if i in heard_at else None,
                )
            )
        competitor_logp: dict[int, dict[Variant, float]] = {}
        for (i, variant, is_accept), lp in zip(index, logp, strict=True):
            if is_accept:
                if lp > out[i].accept_logp:
                    out[i].accept_logp = float(lp)
                    out[i].best_accept = variant
            else:
                competitor_logp.setdefault(i, {})[variant] = float(lp)
        for i, comps in competitor_logp.items():
            base = out[i].accept_logp
            out[i].llr = {v: lp - base for v, lp in comps.items()}
        return Evidence(
            phones=out,
            free_decode=free_names,
            duration_s=len(audio_16k) / 16000.0,
            speech_ratio=len(free_names) / max(1, len(canon_names)),
        )

    def _ctc_logp(
        self, class_lp: np.ndarray, items: list[tuple[int, int, list[int]]]
    ) -> np.ndarray:
        import torch
        import torch.nn.functional as F

        if not items:
            return np.zeros(0, dtype=np.float32)
        device = self.model.device if torch.cuda.is_available() else "cpu"
        lp = torch.from_numpy(class_lp).to(device)
        n_frames = lp.shape[0]
        out = np.empty(len(items), dtype=np.float64)
        for start in range(0, len(items), BATCH):
            chunk = items[start : start + BATCH]
            t_max = max(hi - lo + 1 for lo, hi, _ in chunk)
            s_max = max(1, max(len(t) for _, _, t in chunk))
            frames = torch.zeros((len(chunk), t_max), dtype=torch.long)
            tgt = torch.zeros((len(chunk), s_max), dtype=torch.long)
            in_len = torch.zeros(len(chunk), dtype=torch.long)
            tg_len = torch.zeros(len(chunk), dtype=torch.long)
            for b, (lo, hi, t) in enumerate(chunk):
                length = hi - lo + 1
                frames[b, :length] = torch.arange(lo, hi + 1)
                frames[b, length:] = min(hi, n_frames - 1)
                in_len[b] = length
                tg_len[b] = len(t)
                if t:
                    tgt[b, : len(t)] = torch.tensor(t, dtype=torch.long)
            batch_lp = lp[frames.to(device)].permute(1, 0, 2).contiguous()  # (T, B, C)
            with torch.backends.cudnn.flags(enabled=False):
                loss = F.ctc_loss(
                    batch_lp,
                    tgt.to(device),
                    in_len.to(device),
                    tg_len.to(device),
                    blank=BLANK,
                    reduction="none",
                    zero_infinity=False,
                )
            out[start : start + len(chunk)] = (-loss).double().cpu().numpy()
        out[~np.isfinite(out)] = -1e9
        return out
