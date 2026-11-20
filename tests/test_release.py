"""Release archive (install.cmd): what a user gets, and nothing that must not ship."""

from __future__ import annotations

import hashlib
import re
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import make_release  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[str]]:
    out = tmp_path_factory.mktemp("dist")
    archive = make_release.build(out)
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
    return archive, names


def test_archive_name_and_checksum(built: tuple[Path, list[str]]) -> None:
    archive, names = built
    ver = make_release.version()
    assert archive.name == f"EnglishTeacher-RUS-{ver}.zip"
    assert all(n.startswith(f"EnglishTeacher-RUS-{ver}/") for n in names)
    sums = (archive.parent / "SHA256SUMS.txt").read_text(encoding="ascii").split()
    assert sums == [hashlib.sha256(archive.read_bytes()).hexdigest(), archive.name]
    assert archive.stat().st_size < 5 * 2**20  # code and docs only


def test_everything_the_installer_needs(built: tuple[Path, list[str]]) -> None:
    _archive, names = built
    files = {n.split("/", 1)[1] for n in names}
    required = {
        "install.cmd",
        "uninstall.cmd",
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "trainer/__init__.py",
        "trainer/launcher.py",
        "trainer/espeak_path.py",
        "trainer/uninstall_keys.py",
        "web/index.html",
        "assets/trainer.ico",
    }
    assert required <= files, sorted(required - files)
    # every script the installer and the shortcuts load is there
    for script in ("setup.ps1", "uninstall.ps1", "run.ps1", "make_shortcut.ps1"):
        text = (ROOT / "scripts" / script).read_text(encoding="ascii")
        for ref in [*re.findall(r"\$PSScriptRoot\\([\w.]+)", text), script]:
            assert f"scripts/{ref}" in files, (script, ref)
    for ref in re.findall(r"'([\w.]+\.json)'", (ROOT / "scripts" / "requirements.ps1").read_text()):
        assert f"scripts/{ref}" in files


def test_nothing_that_must_not_ship(built: tuple[Path, list[str]]) -> None:
    _archive, names = built
    files = [n.split("/", 1)[1] for n in names]
    for f in files:
        assert not f.startswith(("tests/", ".claude/", ".venv/", ".tools/", "web/fonts/")), f
        assert not f.startswith(("web/vendor/", "models/", "dist/", "test-results/")), f
        assert not re.search(r"\.(bin|pth|pt|onnx|safetensors|dll|exe|db|lnk|wav)$", f), f
    for local in ("CLAUDE.local.md", "CLAUDE.md", "KICKOFF.md"):
        assert local not in files
    assert not [f for f in files if "espeak" in f.lower() and not f.endswith(".py")]


def test_batch_files_keep_windows_line_endings(built: tuple[Path, list[str]]) -> None:
    """cmd.exe can misread a batch file with LF line endings."""
    archive, _names = built
    ver = make_release.version()
    with zipfile.ZipFile(archive) as z:
        for name in ("install.cmd", "uninstall.cmd"):
            data = z.read(f"EnglishTeacher-RUS-{ver}/{name}")
            assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b""), name
