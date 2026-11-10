"""Downloads pinned models into the models directory. Safe to run repeatedly."""

from __future__ import annotations

import sys

from trainer.model_registry import ALL_MODELS, download, local_path
from trainer.paths import configure_model_env


def main() -> int:
    models_dir = configure_model_env()
    print(f"models directory: {models_dir}")
    for model in ALL_MODELS:
        try:
            path = local_path(model)
            print(f"  {model.key}: present ({model.repo_id}@{model.revision[:8]})")
        except Exception:
            print(
                f"  {model.key}: downloading {model.repo_id}@{model.revision[:8]} ...", flush=True
            )
            path = download(model)
            print(f"  {model.key}: done -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
