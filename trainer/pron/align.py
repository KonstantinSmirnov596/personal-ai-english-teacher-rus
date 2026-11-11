"""CTC forced alignment and sequence alignment helpers (plan items 3.3 and 3.5)."""

from __future__ import annotations

import numpy as np

NEG = -1e30


def forced_align(
    log_probs: np.ndarray, targets: list[int], blank: int = 0
) -> list[tuple[int, int]]:
    """Viterbi alignment of a target sequence to frame log-probabilities.

    log_probs: (T, C). Returns, for each target, the inclusive frame span (first, last) where
    the best CTC path emits it. Raises ValueError when the audio is too short for the targets.
    """
    n_frames = log_probs.shape[0]
    n = len(targets)
    if n == 0:
        return []
    states = np.full(2 * n + 1, blank, dtype=np.int64)
    states[1::2] = targets
    s_count = len(states)
    # a state may be entered from two states back when it is a non-blank that differs from it
    skip = np.zeros(s_count, dtype=bool)
    skip[3::2] = states[3::2] != states[1:-2:2]

    emit = log_probs[:, states]  # (T, S)
    alpha = np.full(s_count, NEG)
    alpha[0] = emit[0, 0]
    alpha[1] = emit[0, 1]
    back = np.zeros((n_frames, s_count), dtype=np.int8)
    for t in range(1, n_frames):
        stay = alpha
        step = np.concatenate(([NEG], alpha[:-1]))
        jump = np.concatenate(([NEG, NEG], alpha[:-2]))
        jump = np.where(skip, jump, NEG)
        cand = np.stack([stay, step, jump])
        choice = cand.argmax(axis=0)
        alpha = cand[choice, np.arange(s_count)] + emit[t]
        back[t] = choice
    end = s_count - 1 if alpha[s_count - 1] >= alpha[s_count - 2] else s_count - 2
    if alpha[end] <= NEG / 2:
        raise ValueError("audio is too short for the reference")
    path = np.empty(n_frames, dtype=np.int64)
    s = end
    for t in range(n_frames - 1, -1, -1):
        path[t] = s
        s -= int(back[t, s])
    spans: list[tuple[int, int]] = []
    for k in range(n):
        frames = np.nonzero(path == 2 * k + 1)[0]
        if len(frames) == 0:
            raise ValueError("alignment failed")
        spans.append((int(frames[0]), int(frames[-1])))
    return spans


def needleman_wunsch(
    ref: list[str], hyp: list[str], sub_cost: float = 1.0, gap_cost: float = 1.0
) -> list[tuple[int | None, int | None]]:
    """Global alignment of two symbol sequences; pairs of indices (None marks a gap)."""
    n, m = len(ref), len(hyp)
    d = np.zeros((n + 1, m + 1))
    d[:, 0] = np.arange(n + 1) * gap_cost
    d[0, :] = np.arange(m + 1) * gap_cost
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i, j] = min(
                d[i - 1, j - 1] + (0.0 if ref[i - 1] == hyp[j - 1] else sub_cost),
                d[i - 1, j] + gap_cost,
                d[i, j - 1] + gap_cost,
            )
    out: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        diag = (
            d[i - 1, j - 1] + (0.0 if ref[i - 1] == hyp[j - 1] else sub_cost) if i and j else None
        )
        if diag is not None and d[i, j] == diag:
            out.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and d[i, j] == d[i - 1, j] + gap_cost:
            out.append((i - 1, None))
            i -= 1
        else:
            out.append((None, j - 1))
            j -= 1
    return out[::-1]
