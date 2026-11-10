"""Stage acceptance: runs the suites a stage needs, checks required tests and reports.

Usage: python scripts/check_stage.py <N>
Exit code 0 only when every check passed. A ``live`` suite that was skipped because no key is
saved does not fail the stage but is listed separately in the summary.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REPORTS = REPO / "docs" / "reports"

MARKERS = {
    "fast": "not gpu and not e2e and not live",
    "gpu": "gpu",
    "e2e": "e2e",
    "live": "live",
}


@dataclass(frozen=True)
class Stage:
    suites: tuple[str, ...]
    required_tests: tuple[str, ...] = ()
    reports: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    optional_suites: tuple[str, ...] = ()


STAGES: dict[int, Stage] = {
    0: Stage(
        suites=("fast", "gpu"),
        required_tests=(
            "tests.test_scaffold::test_package_imports",
            "tests.gpu.test_stage0::test_cuda_device_is_rtx5060",
            "tests.gpu.test_stage0::test_tts_stt_loop_wer",
            "tests.gpu.test_stage0::test_word_probabilities_present",
            "tests.gpu.test_stage0::test_espeak_symbols_in_phoneme_vocab",
        ),
        reports=("stage0.md", "deepseek.md"),
        files=("uv.lock",),
    ),
}


@dataclass
class SuiteResult:
    name: str
    exit_code: int
    passed: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)
    skipped: dict[str, str] = field(default_factory=dict)


def run_suite(name: str, out_dir: Path) -> SuiteResult:
    xml_path = out_dir / f"{name}.xml"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        MARKERS[name],
        f"--junitxml={xml_path}",
        "-o",
        "junit_family=xunit2",
    ]
    print(f"=== suite: {name} ===", flush=True)
    proc = subprocess.run(cmd, cwd=REPO, env=os.environ.copy(), check=False)
    result = SuiteResult(name=name, exit_code=proc.returncode)
    if not xml_path.exists():
        return result
    root = ET.parse(xml_path).getroot()
    for case in root.iter("testcase"):
        test_id = f"{case.get('classname', '')}::{case.get('name', '')}"
        if case.find("failure") is not None or case.find("error") is not None:
            result.failed.add(test_id)
        elif (skip := case.find("skipped")) is not None:
            result.skipped[test_id] = skip.get("message", "")
        else:
            result.passed.add(test_id)
    return result


def _matches(required: str, test_id: str) -> bool:
    # Parametrized tests carry a "[...]" suffix; any parametrization counts.
    return test_id == required or test_id.startswith(required + "[")


_PLACEHOLDER = re.compile(r"\b(TBD|TODO)\b|\?\?\?")


def check_report(path: Path) -> list[str]:
    problems: list[str] = []
    if not path.exists():
        return [f"report missing: {path.relative_to(REPO)}"]
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return [f"report empty: {path.relative_to(REPO)}"]
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if _PLACEHOLDER.search(stripped):
            problems.append(f"{path.name}:{lineno}: placeholder left: {stripped[:80]}")
        if not stripped.startswith("|") or re.fullmatch(r"\|[\s:|-]+\|", stripped):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if any(c == "" for c in cells):
            problems.append(f"{path.name}:{lineno}: table row has an empty cell")
    return problems


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("usage: check_stage.py <N>")
        return 2
    target = int(sys.argv[1])
    if target not in STAGES:
        print(f"stage {target} has no acceptance definition yet")
        return 2

    stages = [STAGES[n] for n in sorted(STAGES) if n <= target]
    suites: list[str] = []
    optional: set[str] = set()
    for st in stages:
        for s in st.suites:
            if s not in suites:
                suites.append(s)
        optional.update(st.optional_suites)
    for s in optional:
        if s not in suites:
            suites.append(s)

    problems: list[str] = []
    notes: list[str] = []
    results: dict[str, SuiteResult] = {}
    with tempfile.TemporaryDirectory(prefix="check_stage_") as tmp:
        for s in suites:
            res = run_suite(s, Path(tmp))
            results[s] = res
            if res.exit_code not in (0, 5):
                problems.append(f"suite {s}: pytest exit code {res.exit_code}")

    for s in optional:
        res = results[s]
        if not res.passed and not res.failed and res.skipped:
            reasons = sorted(set(res.skipped.values()))
            notes.append(f"suite {s} skipped: {'; '.join(reasons)[:200]}")

    all_passed = set().union(*(r.passed for r in results.values()))
    all_seen = all_passed | set().union(*(r.failed | set(r.skipped) for r in results.values()))
    for st in stages:
        for req in st.required_tests:
            if any(_matches(req, t) for t in all_passed):
                continue
            if any(_matches(req, t) for t in all_seen):
                problems.append(f"required test did not pass: {req}")
            else:
                problems.append(f"required test not found: {req}")
        for rep in st.reports:
            problems.extend(check_report(REPORTS / rep))
        for f in st.files:
            if not (REPO / f).exists():
                problems.append(f"required file missing: {f}")

    print()
    print(f"=== stage {target} summary ===")
    for s in suites:
        r = results[s]
        print(
            f"  {s:5}: exit {r.exit_code}, passed {len(r.passed)}, "
            f"failed {len(r.failed)}, skipped {len(r.skipped)}"
        )
    for n in notes:
        print(f"  NOTE: {n}")
    for p in problems:
        print(f"  FAIL: {p}")
    if problems:
        print(f"stage {target}: NOT accepted")
        return 1
    print(f"stage {target}: accepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
