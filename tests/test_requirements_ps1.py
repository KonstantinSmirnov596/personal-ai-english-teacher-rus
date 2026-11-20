"""Installer requirements check (install.cmd): scripts/requirements.ps1 with fake facts.

The PowerShell functions run in Windows PowerShell 5.1 (the shell install.cmd uses) under
``Set-StrictMode -Version Latest``. ``Test-TrRequirements`` only looks at the facts it is given,
so the cases below describe computers this machine is not.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "requirements.ps1"
MESSAGES = ROOT / "scripts" / "requirements.messages.json"

GOOD_GPU = {
    "Name": "NVIDIA GeForce RTX 3060 Laptop GPU",
    "Driver": "610.47",
    "ComputeCap": "8.6",
    "VramTotalMb": 8188,
}


def facts(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "WindowsBuild": 22631,
        "RamTotalMb": 16000,
        "Adapters": ["Intel(R) UHD Graphics", GOOD_GPU["Name"]],
        "Nvidia": dict(GOOD_GPU),
        "Disks": [{"Drive": "C:", "FreeMb": 80_000, "NeedMb": 15_500}],
    }
    base.update(over)
    return base


def gpu(**over: Any) -> dict[str, Any]:
    return {**GOOD_GPU, **over}


CASES: dict[str, dict[str, Any]] = {
    "good": facts(),
    "no_nvidia": facts(Adapters=["Intel(R) UHD Graphics 620"], Nvidia=None),
    "no_adapters": facts(Adapters=[], Nvidia=None),
    "no_driver": facts(Adapters=["NVIDIA GeForce RTX 2060"], Nvidia=None),
    "old_driver": facts(Nvidia=gpu(Driver="566.36")),
    "driver_floor": facts(Nvidia=gpu(Driver="572.61")),
    "driver_below_floor": facts(Nvidia=gpu(Driver="572.60")),
    "pascal": facts(Nvidia=gpu(Name="NVIDIA GeForce GTX 1080", ComputeCap="6.1")),
    "turing": facts(Nvidia=gpu(Name="NVIDIA GeForce RTX 2060", ComputeCap="7.5")),
    "small_vram": facts(Nvidia=gpu(VramTotalMb=4096)),
    "six_gb": facts(Nvidia=gpu(VramTotalMb=6144)),
    "low_disk": facts(Disks=[{"Drive": "C:", "FreeMb": 9000, "NeedMb": 15_500}]),
    "two_drives": facts(
        Disks=[
            {"Drive": "D:", "FreeMb": 60_000, "NeedMb": 5500},
            {"Drive": "C:", "FreeMb": 3000, "NeedMb": 10_000},
        ]
    ),
    "warnings_only": facts(RamTotalMb=8000, WindowsBuild=19044),
}

LANGUAGES = ["ru"]

RUNNER = r"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
. '__SCRIPT__'
$cases = Get-Content -Raw -Encoding UTF8 -Path '__CASES__' | ConvertFrom-Json
$messages = Get-TrMessages
$out = [ordered]@{ cases = [ordered]@{}; languages = [ordered]@{}; invoke = [ordered]@{} }
foreach ($p in $cases.PSObject.Properties) {
    $checks = @(Test-TrRequirements -Facts $p.Value)
    $rows = @()
    foreach ($c in $checks) {
        $texts = [ordered]@{}
        foreach ($lang in @('ru')) {
            $texts[$lang] = Format-TrMessage -Check $c -Messages $messages -Language $lang
        }
        $rows += [ordered]@{
            id = $c.Id; ok = $c.Ok; blocking = $c.Blocking; value = $c.Value
            required = $c.Required; name = $c.Name; message = $c.Message; texts = $texts
        }
    }
    $out.cases[$p.Name] = $rows
}
$out.languages['default'] = Get-TrLanguage
foreach ($name in @('good', 'old_driver', 'warnings_only')) {
    $host_lines = & {
        Invoke-TrRequirements -RepoRoot 'C:\VT' -Language 'ru' -Facts $cases.$name
    } 6>&1
    $result = @($host_lines | Where-Object { $_ -is [bool] })
    $text = @($host_lines | Where-Object { $_ -isnot [bool] } | ForEach-Object { [string]$_ })
    $out.invoke[$name] = [ordered]@{ result = $result[0]; lines = $text }
}
$real = Get-TrFacts -AppDir '__ROOT__' -DataDir (Join-Path $env:TEMP 'tr-requirements-test')
$out.real = [ordered]@{
    build = $real.WindowsBuild; ram = $real.RamTotalMb; disks = @($real.Disks).Count
    adapters = @($real.Adapters).Count; nvidia = ($null -ne $real.Nvidia)
    checks = @(Test-TrRequirements -Facts $real).Count
}
$out | ConvertTo-Json -Depth 8 -Compress
"""

pytestmark = pytest.mark.skipif(
    shutil.which("powershell.exe") is None, reason="Windows PowerShell is not available"
)


@pytest.fixture(scope="module")
def ps(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    tmp = tmp_path_factory.mktemp("requirements")
    cases = tmp / "cases.json"
    cases.write_text(json.dumps(CASES), encoding="utf-8")
    runner = tmp / "runner.ps1"
    code = (
        RUNNER.replace("__SCRIPT__", str(SCRIPT))
        .replace("__CASES__", str(cases))
        .replace("__ROOT__", str(ROOT))
    )
    # UTF-8 with BOM: Windows PowerShell 5.1 reads a file without BOM as the ANSI code page.
    runner.write_text(code, encoding="utf-8-sig")
    proc = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(runner),
        ],
        capture_output=True,
        timeout=120,
        check=False,
        env={k: v for k, v in os.environ.items() if k != "PSModulePath"},
    )
    stdout = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace") + stdout
    last = [line for line in stdout.splitlines() if line.startswith("{")][-1]
    data: dict[str, Any] = json.loads(last)
    return data


def failed(ps: dict[str, Any], case: str) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in ps["cases"][case] if not row["ok"]}


def test_good_machine_passes(ps: dict[str, Any]) -> None:
    assert failed(ps, "good") == {}
    ids = [row["id"] for row in ps["cases"]["good"]]
    assert ids == ["gpu", "driver", "architecture", "vram", "disk", "ram", "windows"]


@pytest.mark.parametrize(
    ("case", "check", "message"),
    [
        ("no_nvidia", "gpu", "gpu"),
        ("no_adapters", "gpu", "gpu"),
        ("no_driver", "driver", "driver_missing"),
        ("old_driver", "driver", "driver"),
        ("driver_below_floor", "driver", "driver"),
        ("pascal", "architecture", "architecture"),
        ("small_vram", "vram", "vram"),
        ("low_disk", "disk", "disk"),
    ],
)
def test_blocking_failures(ps: dict[str, Any], case: str, check: str, message: str) -> None:
    rows = failed(ps, case)
    assert list(rows) == [check], rows
    row = rows[check]
    assert row["blocking"] is True and row["message"] == message


def test_floor_values_pass(ps: dict[str, Any]) -> None:
    assert failed(ps, "driver_floor") == {}
    assert failed(ps, "turing") == {}


def test_each_drive_is_checked(ps: dict[str, Any]) -> None:
    disks = [row for row in ps["cases"]["two_drives"] if row["id"] == "disk"]
    assert [(d["name"], d["ok"]) for d in disks] == [("D:", True), ("C:", False)]


def test_warnings_do_not_block(ps: dict[str, Any]) -> None:
    rows = failed(ps, "warnings_only")
    assert set(rows) == {"ram", "windows"}
    assert not any(r["blocking"] for r in rows.values())


def test_six_gb_card_is_warned_not_blocked(ps: dict[str, Any]) -> None:
    """All models take about 3.6 GB, but the app was tested on 8 GB and has no reduced mode."""
    rows = failed(ps, "six_gb")
    assert list(rows) == ["vram_untested"]
    assert rows["vram_untested"]["blocking"] is False
    assert "8 ГБ" in rows["vram_untested"]["texts"]["ru"]


def test_messages_are_complete_in_russian(ps: dict[str, Any]) -> None:
    table = json.loads(MESSAGES.read_text(encoding="utf-8"))
    assert list(table) == LANGUAGES
    seen = 0
    for case in CASES:
        for row in failed(ps, case).values():
            text = row["texts"]["ru"]
            assert text and not re.search(r"\{\w+\}", text), case
            assert re.search("[а-яё]", text), case  # Russian, not a key name
            seen += 1
    assert seen >= 10
    no_gpu = failed(ps, "no_nvidia")["gpu"]["texts"]
    assert "Intel(R) UHD Graphics 620" in no_gpu["ru"]
    old = failed(ps, "old_driver")["driver"]["texts"]
    assert "566.36" in old["ru"] and "572.61" in old["ru"]


def test_language_is_russian(ps: dict[str, Any]) -> None:
    assert ps["languages"] == {"default": "ru"}


def test_invoke_reports_and_points_to_the_readme(ps: dict[str, Any]) -> None:
    good = ps["invoke"]["good"]
    assert good["result"] is True
    old = ps["invoke"]["old_driver"]
    assert old["result"] is False
    text = "\n".join(old["lines"])
    assert "566.36" in text and "Ничего не скачано" in text
    assert r"C:\VT\README.md" in text
    warn = ps["invoke"]["warnings_only"]
    assert warn["result"] is True
    assert any("README.md" in line for line in warn["lines"])


def test_real_facts_of_this_computer(ps: dict[str, Any]) -> None:
    real = ps["real"]
    assert real["build"] > 0 and real["ram"] > 0 and real["disks"] >= 1
    assert real["checks"] >= 5


def test_setup_checks_before_any_download() -> None:
    setup = (ROOT / "scripts" / "setup.ps1").read_text(encoding="utf-8")
    check = setup.index("Invoke-TrRequirements")
    for download in ("Install-TrUv", "python install", "sync --locked", "setup_models"):
        pos = setup.find(download)
        if pos >= 0:
            assert check < pos, download


def test_script_is_ascii() -> None:
    """Windows PowerShell 5.1 reads a script without BOM in the ANSI code page."""
    SCRIPT.read_text(encoding="ascii")
    (ROOT / "scripts" / "setup.ps1").read_text(encoding="ascii")
