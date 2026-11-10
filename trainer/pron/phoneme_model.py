"""Phoneme recognizer: facebook/wav2vec2-lv-60-espeak-cv-ft (CTC over espeak phoneme symbols)."""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from trainer.model_registry import PHONEME, local_path
from trainer.speech.audio import FloatAudio


class PhonemeModel:
    def __init__(self, device: str = "cuda") -> None:
        self.device = device
        self._model: Any = None
        self.vocab: dict[str, int] = {}
        self.id_to_token: dict[int, str] = {}
        self.blank_id = 0

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import Wav2Vec2ForCTC

        root = local_path(PHONEME)
        self.vocab = json.loads((root / "vocab.json").read_text(encoding="utf-8"))
        self.id_to_token = {i: t for t, i in self.vocab.items()}
        self.blank_id = self.vocab.get("<pad>", 0)
        model = Wav2Vec2ForCTC.from_pretrained(str(root), dtype=torch.float16)
        self._model = model.to(self.device).eval()

    def unload(self) -> None:
        if self._model is None:
            return
        import torch

        self._model = None
        torch.cuda.empty_cache()

    def log_probs(self, audio_16k: FloatAudio) -> np.ndarray:
        """Frame-level log-probabilities, shape (frames, vocab); one frame is 20 ms."""
        import torch

        self.load()
        x = np.asarray(audio_16k, dtype=np.float32)
        x = (x - x.mean()) / (x.std() + 1e-7)  # matches the feature extractor (do_normalize)
        with torch.inference_mode():
            inp = torch.from_numpy(x).to(self.device, dtype=torch.float16)[None, :]
            logits = self._model(inp).logits[0].float()
            return torch.log_softmax(logits, dim=-1).cpu().numpy()

    def greedy_decode(self, log_probs: np.ndarray) -> list[str]:
        """Free phoneme recognition: best path with CTC collapse."""
        ids = log_probs.argmax(axis=-1)
        out: list[str] = []
        prev = -1
        for i in ids:
            if i != prev and i != self.blank_id:
                tok = self.id_to_token.get(int(i), "")
                if tok and not tok.startswith("<"):
                    out.append(tok)
            prev = int(i)
        return out
