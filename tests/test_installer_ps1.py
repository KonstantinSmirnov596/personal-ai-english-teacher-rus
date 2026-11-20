"""Installer helpers (install.cmd): scripts/installer.ps1 with fake inputs.

Runs the PowerShell functions in Windows PowerShell 5.1 under ``Set-StrictMode -Version Latest``:
folder checks (spaces, Cyrillic letters, length, write access), the uv download with its SHA-256,
the console question without a console, and the order of the steps in setup.ps1.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

pytestmark = pytest.mark.skipif(
    shutil.which("powershell.exe") is None, reason="Windows PowerShell is not available"
)

RUNNER = r"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
. '__SCRIPTS__\_common.ps1'
. '__SCRIPTS__\requirements.ps1'
. '__SCRIPTS__\installer.ps1'
$out = [ordered]@{}
function Ids($list) { return @($list | Where-Object { $_ } | ForEach-Object { $_.Id }) }
$asciiShort = { param($p) 'C:\Users\D4C4~1\TRAINE~1' }
$ok = { param($p) $true }
$a = @{ ShortPath = { param($p) '' }; LongPaths = $false; Writable = $ok }
$data = 'D:\Home\ivan\AppData\Local\EnglishTrainer'
$out.plain = Ids (Test-TrInstallPath -AppDir 'C:\EnglishTeacherRUS' -DataDir $data @a)
$out.spaces = Ids (Test-TrInstallPath -AppDir 'D:\Home\ivan\My Apps\Trainer' -DataDir $data @a)
$jose = 'C:\Users\Иван\Тренажёр'
$b = @{ ShortPath = $asciiShort; LongPaths = $false; Writable = $ok }
$out.accent_short = Ids (Test-TrInstallPath -AppDir $jose -DataDir 'C:\Users\Иван\AppData' @b)
$out.accent_noshort = Ids (Test-TrInstallPath -AppDir $jose -DataDir $data @a)
$long = 'C:\' + ('a' * 90)
$out.long = Ids (Test-TrInstallPath -AppDir $long -DataDir $data @a)
$c = @{ ShortPath = { param($p) '' }; LongPaths = $true; Writable = $ok }
$out.long_enabled = Ids (Test-TrInstallPath -AppDir $long -DataDir $data @c)
$d = @{ ShortPath = { param($p) '' }; LongPaths = $false; Writable = { param($p) $p -ne 'C:\VT' } }
$out.readonly = Ids (Test-TrInstallPath -AppDir 'C:\VT' -DataDir $data @d)
$out.real_writable = Test-TrWritable '__TMP__\writable'
$out.format = Format-TrText 'a {x} b {y}' @{ x = 1; y = 'z' }
$out.yesno_default = Read-TrYesNo -Question 'q?' -YesLetters 'DY' -Default $false
$out.shortcut_name = Get-TrShortcutName
$uv = Install-TrUv -ToolsDir '__TMP__\tools' -Url '__ZIPURL__' -Sha256 '__SHA__' -Version '9.9.9'
$out.uv_path = $uv
$out.uv_exists = Test-Path $uv
try {
    $bad = @{ Url = '__ZIPURL__'; Sha256 = ('0' * 64); Version = '9.9.9' }
    Install-TrUv -ToolsDir '__TMP__\tools2' @bad | Out-Null
    $out.bad_sha = 'no error'
} catch {
    $out.bad_sha = $_.Exception.Message
}
$out.bad_sha_zip_left = Test-Path '__TMP__\tools2\downloads\uv-9.9.9.zip'
$out | ConvertTo-Json -Depth 5 -Compress
"""


@pytest.fixture(scope="module")
def ps(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    tmp = tmp_path_factory.mktemp("installer")
    zip_path = tmp / "uv.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("uv-x86_64-pc-windows-msvc/uv.exe", b"not really uv")
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    code = (
        RUNNER.replace("__SCRIPTS__", str(SCRIPTS))
        .replace("__TMP__", str(tmp))
        .replace("__ZIPURL__", zip_path.as_uri())
        .replace("__SHA__", sha)
    )
    runner = tmp / "runner.ps1"
    runner.write_text(code, encoding="utf-8-sig")
    proc = subprocess.run(
        [
            *("powershell.exe", "-NoProfile", "-NonInteractive"),
            *("-ExecutionPolicy", "Bypass", "-File", str(runner)),
        ],
        capture_output=True,
        timeout=120,
        check=False,
        stdin=subprocess.DEVNULL,
        env={k: v for k, v in os.environ.items() if k != "PSModulePath"},
    )
    stdout = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace") + stdout
    data: dict[str, Any] = json.loads([x for x in stdout.splitlines() if x.startswith("{")][-1])
    data["tmp"] = str(tmp)
    return data


def ids(value: Any) -> list[str]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def test_folder_checks(ps: dict[str, Any]) -> None:
    assert ids(ps["plain"]) == []
    assert ids(ps["spaces"]) == []  # spaces are fine
    assert ids(ps["accent_short"]) == []  # an ASCII short name makes espeak-ng work anywhere
    assert ids(ps["accent_noshort"]) == ["path_ascii"]
    assert ids(ps["long"]) == ["path_long"]
    assert ids(ps["long_enabled"]) == []
    assert ids(ps["readonly"]) == ["path_unwritable"]
    assert ps["real_writable"] is True


def test_helpers(ps: dict[str, Any]) -> None:
    assert ps["format"] == "a 1 b z"
    assert ps["yesno_default"] is False  # no console: the default answer
    assert ps["shortcut_name"] == "Тренажёр"


def test_uv_download_is_verified(ps: dict[str, Any]) -> None:
    assert ps["uv_exists"] is True
    assert ps["uv_path"].endswith(r"tools\uv-9.9.9\uv.exe")
    assert ps["bad_sha"] == "checksum:uv 9.9.9"
    assert ps["bad_sha_zip_left"] is False  # a file with the wrong hash is deleted


def test_setup_order_and_pins() -> None:
    setup = (SCRIPTS / "setup.ps1").read_text(encoding="ascii")
    order = [
        "Invoke-TrRequirements",
        "Test-TrInstallPath",
        "Install-TrUv",
        "python install",
        "sync --locked",
        "trainer.setup_models",
        "trainer.setup_fonts",
        "make_shortcut.ps1",
        "trainer.launcher",
    ]
    positions = [setup.index(x) for x in order]
    assert positions == sorted(positions), list(zip(order, positions, strict=True))
    assert "--no-bin --no-registry" in setup
    assert "sync --locked --no-dev" in setup  # users get no test tools
    installer = (SCRIPTS / "installer.ps1").read_text(encoding="ascii")
    assert "$script:TrUvVersion = '0.13.7'" in installer
    assert "0cdc5b38fe5a8168ed6a25814f5ba43c21d313dd3187314fccb37b7cec46e021" in installer
    assert "py -3" not in setup  # no system Python
    # Windows PowerShell 5.1 stops a native command piped into Select-Object -First early, and
    # its exit code then reads -1 (the first clean install into a Cyrillic folder failed so).
    native_first = r"&\s*\$\w+[^\n|]*\|\s*Select-Object\s+-First"
    for name in ("setup.ps1", "installer.ps1", "uninstall.ps1", "requirements.ps1"):
        text = (SCRIPTS / name).read_text(encoding="ascii")
        assert not re.search(native_first, text), name


def test_cmd_wrappers() -> None:
    for name, script in (("install.cmd", "setup.ps1"), ("uninstall.cmd", "uninstall.ps1")):
        text = (ROOT / name).read_text(encoding="ascii")
        assert f"scripts\\{script}" in text
        assert "-ExecutionPolicy Bypass" in text and "-NoProfile" in text


def test_scripts_are_ascii() -> None:
    """Windows PowerShell 5.1 reads a script without BOM in the ANSI code page."""
    for name in ("setup.ps1", "installer.ps1", "uninstall.ps1", "_common.ps1", "requirements.ps1"):
        (SCRIPTS / name).read_text(encoding="ascii")


def test_uninstall_asks_before_data(ps: dict[str, Any]) -> None:
    text = (SCRIPTS / "uninstall.ps1").read_text(encoding="ascii")
    assert "uninstall_data_question" in text and "-Default $false" in text
    assert text.index("uninstall_keys") < text.index("Remove-Item -Recurse -Force $dir")


def test_every_script_parses_in_windows_powershell() -> None:
    code = (
        "$bad = @(); foreach ($f in Get-ChildItem -Path '" + str(SCRIPTS) + "' -Filter *.ps1) {"
        " $tokens = $null; $errs = $null;"
        " [void][System.Management.Automation.Language.Parser]::ParseFile("
        "$f.FullName, [ref]$tokens, [ref]$errs);"
        " foreach ($e in $errs) { $bad += ($f.Name + ': ' + $e.Message) } };"
        " if ($bad.Count) { $bad; exit 1 } else { 'ok' }"
    )
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", code],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0 and proc.stdout.strip() == "ok", proc.stdout + proc.stderr


def test_shortcuts_in_cyrillic_folders(tmp_path: Path) -> None:
    """WScript.Shell goes through the ANSI code page; the Unicode helper saves a shortcut in any
    folder and its target reads back unchanged (scripts/shortcut.ps1)."""
    folder = tmp_path / "Иван Ж" / "Тренажёр"
    folder.mkdir(parents=True)
    target = folder / "pythonw.exe"
    target.write_bytes(b"")
    link = folder / "Тренажёр.lnk"
    code = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        f". '{SCRIPTS / 'shortcut.ps1'}'; "
        f"New-TrShortcut -Path '{link}' -Target '{target}' -Arguments '-m trainer.launcher' "
        f"-WorkDir '{folder}' -Icon ''; "
        f"Get-TrShortcutTarget '{link}'"
    )
    runner = tmp_path / "runner.ps1"
    runner.write_text(code, encoding="utf-8-sig")
    proc = subprocess.run(
        [
            *("powershell.exe", "-NoProfile", "-NonInteractive"),
            *("-ExecutionPolicy", "Bypass", "-File", str(runner)),
        ],
        capture_output=True,
        timeout=120,
        check=False,
        env={k: v for k, v in os.environ.items() if k != "PSModulePath"},
    )
    out = proc.stdout.decode("utf-8", "replace").strip()
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert link.exists()
    assert out.splitlines()[-1] == str(target), out


def test_stage_check_runs_the_developer_setup() -> None:
    """The acceptance run must neither drop the test tools nor touch the Desktop."""
    text = (SCRIPTS / "check_stage.ps1").read_text(encoding="ascii")
    assert "setup.ps1') -Dev -Shortcuts no -NoLaunch" in text
