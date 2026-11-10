"""Makes the CUDA 12 / cuDNN 9 DLLs visible to CTranslate2 on Windows.

faster-whisper (CTranslate2) needs ``cublas64_12.dll`` and the cuDNN 9 libraries. The pinned torch
build (``+cu128``) ships exactly these in ``torch/lib``; we expose that directory to the Windows
loader both through ``os.add_dll_directory`` (for ``ctypes``) and ``PATH`` (cuDNN loads its
sub-libraries by name), instead of asking the owner to install the CUDA toolkit.
"""

from __future__ import annotations

import os
import sys
from importlib.util import find_spec
from pathlib import Path

_prepared = False


def torch_lib_dir() -> Path | None:
    spec = find_spec("torch")
    if spec is None or spec.origin is None:
        return None
    lib = Path(spec.origin).parent / "lib"
    return lib if lib.is_dir() else None


def prepare_cuda_dlls() -> None:
    global _prepared
    if _prepared or sys.platform != "win32":
        _prepared = True
        return
    lib = torch_lib_dir()
    if lib is not None:
        os.add_dll_directory(str(lib))
        path = os.environ.get("PATH", "")
        if str(lib) not in path.split(os.pathsep):
            os.environ["PATH"] = str(lib) + os.pathsep + path
    _prepared = True
