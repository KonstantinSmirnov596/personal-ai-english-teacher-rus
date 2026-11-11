"""Installs third-party window assets into web/vendor at setup time.

- Silero VAD for the browser (@ricky0123/vad-web, ISC): bundle, audio worklet, ONNX models.
- ONNX Runtime Web (onnxruntime-web, MIT): WASM build used by vad-web.

Packages come from the npm registry, pinned by version and verified by sha512 integrity.
The core serves these files locally; the window never loads anything from the network.
"""

from __future__ import annotations

import base64
import hashlib
import io
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

VENDOR = Path(__file__).resolve().parent.parent / "web" / "vendor"


@dataclass(frozen=True)
class Package:
    slug: str
    url: str
    integrity: str
    files: tuple[str, ...]  # paths inside the tarball, relative to package/


PACKAGES = (
    Package(
        slug="vad",
        url="https://registry.npmjs.org/@ricky0123/vad-web/-/vad-web-0.0.31.tgz",
        integrity="sha512-ePVq8TvB21sdHSxVMZOEIf9JVa7MAYpaIaNXeGgsPOiMCN8xbPCdF2LS5aYj7VRR3ZxzutedgV9ZplV65IA1Fg==",
        files=(
            "dist/bundle.min.js",
            "dist/bundle.min.js.LICENSE.txt",
            "dist/vad.worklet.bundle.min.js",
            "dist/silero_vad_legacy.onnx",
            "dist/silero_vad_v5.onnx",
            "LICENSE",
        ),
    ),
    Package(
        slug="ort",
        url="https://registry.npmjs.org/onnxruntime-web/-/onnxruntime-web-1.31.1.tgz",
        integrity="sha512-5GeH+bWQ+HxM10z7erGlsWqryrwMbSvm/1tkIsKTT48g0fI19VXVVC435t4t7VsPdSQeS7NmuZPXrQ73k82URw==",
        files=(
            "dist/ort.wasm.min.js",
            "dist/ort-wasm-simd-threaded.wasm",
            "dist/ort-wasm-simd-threaded.mjs",
            "LICENSE",
        ),
    ),
)


def _verify(data: bytes, integrity: str) -> None:
    algo, _, expected = integrity.partition("-")
    if base64.b64encode(hashlib.new(algo, data).digest()).decode() != expected:
        raise RuntimeError("vendor package integrity check failed")


def _installed(pkg: Package) -> bool:
    return all(
        (VENDOR / pkg.slug / Path(f).name).exists() for f in pkg.files if Path(f).name != "LICENSE"
    )


def install(pkg: Package) -> None:
    import httpx

    data = httpx.get(pkg.url, timeout=120, follow_redirects=True).content
    _verify(data, pkg.integrity)
    target = VENDOR / pkg.slug
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        names = set(tar.getnames())
        for rel in pkg.files:
            member_name = f"package/{rel}"
            if member_name not in names:
                if rel == "LICENSE":
                    continue  # some packages ship the license only inside the bundle comment
                raise RuntimeError(f"{pkg.slug}: {rel} missing in package")
            member = tar.extractfile(member_name)
            assert member is not None
            name = Path(rel).name
            if name == "LICENSE":
                name = "LICENSE.txt"
            (target / name).write_bytes(member.read())


def main() -> int:
    for pkg in PACKAGES:
        if _installed(pkg):
            print(f"vendor: {pkg.slug} present")
            continue
        install(pkg)
        print(f"vendor: {pkg.slug} installed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
