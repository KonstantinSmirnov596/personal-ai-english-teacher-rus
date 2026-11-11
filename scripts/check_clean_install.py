"""Stage 4.1: setup from scratch on a clean copy of the repository in another directory.

Copies the tracked and new (not ignored) files to a temporary directory, runs
``scripts\\setup.ps1`` there with Windows PowerShell, then checks that the copy works: the fast
test suite passes and the core starts, loads the models and synthesizes speech.

    .venv\\Scripts\\python.exe scripts\\check_clean_install.py [--keep]
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def step(text: str) -> None:
    print(f"--- clean install: {text}", flush=True)


def copy_repo(dest: Path) -> int:
    out = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout
    files = [f for f in out.decode("utf-8").split("\0") if f]
    for rel in files:
        src = REPO / rel
        if not src.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    return len(files)


def _force_remove(func, path, _exc) -> None:  # noqa: ANN001
    os.chmod(path, stat.S_IWRITE)
    func(path)


def smoke(dest: Path, python: Path) -> None:
    """Start the copied app, wait for the models, synthesize a reference phrase."""
    import httpx

    data = dest / "_data"
    env = os.environ.copy()
    env["TRAINER_DATA_DIR"] = str(data)
    env["TRAINER_MODELS_DIR"] = os.environ.get("TRAINER_MODELS_DIR") or str(
        Path(os.environ["LOCALAPPDATA"]) / "EnglishTrainer" / "models"
    )
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("HF_HUB_OFFLINE", None)
    proc = subprocess.Popen(
        [str(python), "-m", "trainer.launcher", "--no-window", "--port", "0"],
        cwd=dest,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        assert proc.stdout is not None
        url = ""
        deadline = time.monotonic() + 120
        for line in proc.stdout:
            if line.startswith("TRAINER_URL "):
                url = line.split(" ", 1)[1].strip()
                break
            if time.monotonic() > deadline:
                break
        if not url:
            raise RuntimeError("the copied app did not start")
        base = url.split("/?t=")[0]
        with httpx.Client(base_url=base, timeout=60, headers={"Origin": base}) as client:
            first = client.get(url.replace(base, ""), follow_redirects=False)
            if first.status_code != 303:
                raise RuntimeError(f"launch token rejected: {first.status_code}")
            deadline = time.monotonic() + 240
            while True:
                health = client.get("/api/health").json()
                if health["status"] == "ok":
                    break
                if health["status"] == "error" or time.monotonic() > deadline:
                    raise RuntimeError(f"models are not ready: {health}")
                time.sleep(0.5)
            wav = client.get("/api/pron/reference?phrase_id=p01")
            if wav.status_code != 200 or len(wav.content) < 50_000:
                raise RuntimeError("speech synthesis failed in the copied app")
            if (
                client.get("/").status_code != 200
                or client.get("/fonts/fonts.css").status_code != 200
            ):
                raise RuntimeError("window files are not served")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the copy for inspection")
    args = ap.parse_args()
    dest = Path(tempfile.mkdtemp(prefix="trainer-clean-"))
    started = time.monotonic()
    ok = False
    try:
        step(f"copy -> {dest}")
        count = copy_repo(dest)
        print(f"{count} files")
        for must_not_exist in (".venv", ".tools", "web/fonts", "web/vendor"):
            if (dest / must_not_exist).exists():
                raise RuntimeError(f"{must_not_exist} leaked into the clean copy")

        step("scripts\\setup.ps1 (Windows PowerShell)")
        env = os.environ.copy()
        for name in ("UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV", "HF_HOME"):
            env.pop(name, None)
        setup = subprocess.run(
            ["powershell.exe", "-NoProfile", "-File", str(dest / "scripts" / "setup.ps1")],
            cwd=dest,
            env=env,
            check=False,
        )
        if setup.returncode != 0:
            raise RuntimeError(f"setup.ps1 failed with exit code {setup.returncode}")
        python = dest / ".venv" / "Scripts" / "python.exe"
        expected = [
            python,
            dest / ".venv" / "Scripts" / "pythonw.exe",
            dest / "web" / "fonts" / "fonts.css",
            dest / "web" / "vendor" / "vad" / "bundle.min.js",
            dest / "web" / "vendor" / "ort" / "ort-wasm-simd-threaded.wasm",
            dest / "uv.lock",
        ]
        missing = [str(p) for p in expected if not p.exists()]
        if missing or not list(dest.glob("*.lnk")):
            raise RuntimeError(f"setup left things missing: {missing or 'shortcut'}")

        step("fast test suite in the copy")
        tests = subprocess.run(
            [str(python), "-m", "pytest", "-q", "-m", "not gpu and not e2e and not live", "-x"],
            cwd=dest,
            env=env,
            check=False,
        )
        if tests.returncode != 0:
            raise RuntimeError("fast tests failed in the clean copy")

        step("start the copied app, load models, synthesize speech")
        smoke(dest, python)
        ok = True
    except Exception as e:
        print(f"clean install: FAILED: {e}", flush=True)
    finally:
        if args.keep:
            print(f"copy kept at {dest}")
        else:
            shutil.rmtree(dest, onexc=_force_remove)
    print(f"clean install: {'OK' if ok else 'FAILED'} in {time.monotonic() - started:.0f} s")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
