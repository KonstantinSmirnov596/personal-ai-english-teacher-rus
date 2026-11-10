"""Installs the window fonts (Inter, JetBrains Mono; OFL-1.1) into web/fonts at setup time.

The packages come from the npm registry (Fontsource builds), pinned by version and verified by
their sha512 integrity. Only Latin and Cyrillic subsets in WOFF2 are kept. Safe to run again.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

WEB_FONTS = Path(__file__).resolve().parent.parent / "web" / "fonts"
SUBSETS = ("latin", "latin-ext", "cyrillic", "cyrillic-ext")


@dataclass(frozen=True)
class FontPackage:
    slug: str
    url: str
    integrity: str
    weights: tuple[int, ...]


PACKAGES = (
    FontPackage(
        slug="inter",
        url="https://registry.npmjs.org/@fontsource/inter/-/inter-5.3.0.tgz",
        integrity="sha512-RofMylZmjlJEfELXeNHFWBRcSs75rGU/6bV2S2jfnvv/3rPXPGe0LgUJTklcHZ9lM4OZmAVFhcJPnACfb91A3g==",
        weights=(400, 500, 600),
    ),
    FontPackage(
        slug="jetbrains-mono",
        url="https://registry.npmjs.org/@fontsource/jetbrains-mono/-/jetbrains-mono-5.3.0.tgz",
        integrity="sha512-fqDfB5I9f1p1TV486aUgB9t8zP84P0O1FtQR5Ol9vjwPy+S+EIGlVYm1cvj2W5shcZMTg2nZFdVMoH5wFu8a1A==",
        weights=(400, 500),
    ),
)

_BLOCK = re.compile(r"/\* ([\w-]+) \*/\s*(@font-face \{.*?\})", re.DOTALL)
_SRC = re.compile(r"src: [^;]+;")


def _verify(data: bytes, integrity: str) -> None:
    algo, _, expected = integrity.partition("-")
    digest = base64.b64encode(hashlib.new(algo, data).digest()).decode()
    if digest != expected:
        raise RuntimeError("font package integrity check failed")


def _install(pkg: FontPackage) -> list[str]:
    import httpx

    data = httpx.get(pkg.url, timeout=60, follow_redirects=True).content
    _verify(data, pkg.integrity)
    target = WEB_FONTS / pkg.slug
    target.mkdir(parents=True, exist_ok=True)
    css_blocks: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        lic = tar.extractfile("package/LICENSE")
        if lic is not None:
            (target / "LICENSE.txt").write_bytes(lic.read())
        for weight in pkg.weights:
            member = tar.extractfile(f"package/{weight}.css")
            if member is None:
                raise RuntimeError(f"{pkg.slug}: {weight}.css missing")
            css = member.read().decode("utf-8")
            for name, block in _BLOCK.findall(css):
                subset = name.removeprefix(f"{pkg.slug}-").removesuffix(f"-{weight}-normal")
                if subset not in SUBSETS:
                    continue
                font_file = f"{name}.woff2"
                font = tar.extractfile(f"package/files/{font_file}")
                if font is None:
                    raise RuntimeError(f"{pkg.slug}: {font_file} missing")
                (target / font_file).write_bytes(font.read())
                src = f"src: url(/fonts/{pkg.slug}/{font_file}) format('woff2');"
                css_blocks.append(f"/* {name} */\n" + _SRC.sub(src, block))
    return css_blocks


def main() -> int:
    css_path = WEB_FONTS / "fonts.css"
    if css_path.exists() and all((WEB_FONTS / p.slug / "LICENSE.txt").exists() for p in PACKAGES):
        print("fonts: present")
        return 0
    blocks: list[str] = [
        "/* Inter and JetBrains Mono, SIL Open Font License 1.1 (see LICENSE.txt) */"
    ]
    for pkg in PACKAGES:
        blocks.extend(_install(pkg))
        print(f"fonts: {pkg.slug} installed")
    css_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
