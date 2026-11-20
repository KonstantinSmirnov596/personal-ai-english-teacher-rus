"""Release packaging: ``dist/EnglishTeacher-RUS-<version>.zip`` and ``SHA256SUMS.txt``.

The archive holds what a user needs to run ``install.cmd``: the app code, the installer scripts,
the lock file, the README and the notices. It never holds model weights, a Python environment,
downloaded tools, fonts or the voice detector (the installer fetches them, pinned and
checksummed), espeak-ng or other binaries, databases or shortcuts. Developer-only files (tests,
benchmarks, stage checks, raw measurement data) stay in the repository.

    .venv\\Scripts\\python.exe scripts\\make_release.py [--out dist]
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Developer-only files and folders (repository-relative, POSIX).
EXCLUDE_PREFIXES = (
    ".claude/",
    "tests/",
    "docs/reports/data/",
)
EXCLUDE_FILES = {
    "CLAUDE.md",
    "KICKOFF.md",
    ".gitattributes",
    ".gitignore",
    "scripts/bench.py",
    "scripts/bench_turn.py",
    "scripts/check_clean_install.py",
    "scripts/check_stage.ps1",
    "scripts/check_stage.py",
    "scripts/make_icon.py",
    "scripts/make_release.py",
    "scripts/phoneme_probe.py",
    "scripts/probe_edge.py",
    "scripts/pron_eval.py",
    "scripts/test.ps1",
    "scripts/uia_click.ps1",
}
# Never shipped, whatever the file list says.
FORBIDDEN = re.compile(
    r"(^|/)(\.venv|\.tools|web/fonts|web/vendor|models|dist|test-results)/"
    r"|\.(bin|pth|pt|onnx|safetensors|dll|exe|so|db|db-wal|db-shm|lnk|wav)$",
    re.IGNORECASE,
)
LOCAL_ONLY = {"CLAUDE.local.md"}


def version() -> str:
    text = (REPO / "trainer" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__ = "([^"]+)"', text)
    if not m:
        raise SystemExit("version not found in trainer/__init__.py")
    return m.group(1)


def repository_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    return sorted(f for f in out.split("\0") if f and (REPO / f).is_file())


def release_files() -> list[str]:
    files = []
    for f in repository_files():
        if f in LOCAL_ONLY or f in EXCLUDE_FILES or f.startswith(EXCLUDE_PREFIXES):
            continue
        if FORBIDDEN.search(f):
            raise SystemExit(f"refusing to package {f}")
        files.append(f)
    return files


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def build(out_dir: Path) -> Path:
    ver = version()
    name = f"EnglishTeacher-RUS-{ver}"
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"{name}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in release_files():
            z.write(REPO / f, f"{name}/{f}")
    (out_dir / "SHA256SUMS.txt").write_text(
        f"{sha256(archive)}  {archive.name}\n", encoding="ascii"
    )
    notes = REPO / "docs" / "release-notes" / f"v{ver}.md"
    if notes.exists():
        shutil.copyfile(notes, out_dir / f"release-notes-v{ver}.md")
    return archive


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=REPO / "dist")
    args = ap.parse_args()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.strip()
    if dirty:
        print("warning: the working tree has uncommitted changes", file=sys.stderr)
    archive = build(args.out)
    size = archive.stat().st_size / 2**20
    print(f"{archive} ({size:.1f} MB)")
    print((args.out / "SHA256SUMS.txt").read_text(encoding="ascii").strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
