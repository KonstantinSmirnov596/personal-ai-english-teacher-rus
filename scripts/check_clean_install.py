"""Clean installation from the release archive (install.cmd).

1. ``make_release`` builds ``EnglishTeacher-RUS-<version>.zip``; it is extracted into a folder
   whose path has a space and Cyrillic letters, as under a Russian Windows user name (and stays
   under the installer's 80-character limit).
2. ``LOCALAPPDATA`` and ``TEMP`` point to new empty folders (also with a space and Cyrillic
   letters), so nothing from this machine is reused: uv, Python, packages and models are
   downloaded and verified again by ``install.cmd -Shortcuts no -NoLaunch``; a second run finds
   everything in place.
3. The installed app starts, loads the models, synthesizes speech and checks a reading (espeak-ng
   runs from the Cyrillic folder through its short name).
4. ``uninstall.cmd -Yes -DeleteData`` removes the environment, tools and data.

A run from an archive downloaded with a browser (Mark of the Web) is a manual check.

This downloads about 7 GB. ``--uv-cache <dir>`` reuses a package cache for repeated runs (the
models are always downloaded).

    .venv\\Scripts\\python.exe scripts\\check_clean_install.py [--keep] [--uv-cache <dir>]
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
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Cyrillic folder names in the output, also when it goes to a log file.
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
sys.path.insert(0, str(REPO / "scripts"))


def step(text: str) -> None:
    print(f"--- clean install: {text}", flush=True)


def _force_remove(func, path, _exc) -> None:  # noqa: ANN001
    os.chmod(path, stat.S_IWRITE)
    func(path)


def clean_env(local: Path, temp: Path, uv_cache: str | None) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("TRAINER_", "UV_", "HF_", "VIRTUAL_ENV", "PYTHON"))
    }
    env.update({"LOCALAPPDATA": str(local), "TEMP": str(temp), "TMP": str(temp)})
    # Never the owner's real credential service: uninstall -DeleteData deletes keys.
    env["TRAINER_KEYRING_SERVICE"] = f"EnglishTrainer-test-{os.getpid()}"
    if uv_cache:
        env["UV_CACHE_DIR"] = uv_cache
    return env


def smoke(app: Path, env: dict[str, str]) -> None:
    import httpx

    python = app / ".venv" / "Scripts" / "python.exe"
    proc = subprocess.Popen(
        [str(python), "-m", "trainer.launcher", "--no-window", "--port", "0"],
        cwd=app,
        env=dict(env, PYTHONUNBUFFERED="1"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        assert proc.stdout is not None
        url = ""
        for line in proc.stdout:
            if line.startswith("TRAINER_URL "):
                url = line.split(" ", 1)[1].strip()
                break
        if not url:
            raise RuntimeError("the installed app did not start")
        base = url.split("/?t=")[0]
        with httpx.Client(base_url=base, timeout=120, headers={"Origin": base}) as client:
            if client.get(url.replace(base, ""), follow_redirects=False).status_code != 303:
                raise RuntimeError("launch token rejected")
            deadline = time.monotonic() + 300
            while (health := client.get("/api/health").json())["status"] != "ok":
                if health["status"] == "error" or time.monotonic() > deadline:
                    raise RuntimeError(f"models are not ready: {health}")
                time.sleep(0.5)
            wav = client.get("/api/pron/reference?phrase_id=p01")
            if wav.status_code != 200 or len(wav.content) < 50_000:
                raise RuntimeError("speech synthesis failed")
            r = client.post(
                "/api/pron/assess?phrase_id=p01",
                content=wav.content,
                headers={"Content-Type": "audio/wav"},
            )
            if r.status_code != 200 or not r.json().get("words"):
                raise RuntimeError(f"reading check failed: {r.status_code} {r.text[:200]}")
            for path in ("/", "/fonts/fonts.css", "/vendor/vad/bundle.min.js"):
                if client.get(path).status_code != 200:
                    raise RuntimeError(f"window file not served: {path}")
            print(f"    models: {health['models']}", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the folders for inspection")
    ap.add_argument("--uv-cache", default=None, help="reuse this uv package cache")
    args = ap.parse_args()

    import make_release

    root = Path(tempfile.mkdtemp(prefix="tr-"))
    started = time.monotonic()
    ok = False
    try:
        step("release archive")
        archive = make_release.build(root / "dist")
        target = root / "Иван Ж"  # Cyrillic and a space, short enough for the path check
        with zipfile.ZipFile(archive) as z:
            z.extractall(target)
            app = target / z.namelist()[0].split("/", 1)[0]
        print(f"    app folder: {app}")
        local = root / "Локальные данные"
        temp = root / "temp"
        local.mkdir()
        temp.mkdir()
        env = clean_env(local, temp, args.uv_cache)

        step("install.cmd")
        install = subprocess.run(
            ["cmd.exe", "/c", str(app / "install.cmd"), "-Shortcuts", "no", "-NoLaunch"],
            cwd=app,
            env=env,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        if install.returncode != 0:
            raise RuntimeError(f"install.cmd failed with exit code {install.returncode}")
        expected = [
            app / ".venv" / "Scripts" / "pythonw.exe",
            app / ".tools" / "python",
            app / "web" / "fonts" / "fonts.css",
            app / "web" / "vendor" / "vad" / "bundle.min.js",
            app / "Тренажёр.lnk",
        ]
        missing = [str(p) for p in expected if not p.exists()]
        if missing:
            raise RuntimeError(f"installation incomplete: {missing}")
        if list((local / "EnglishTrainer" / "models").rglob("*.incomplete")):
            raise RuntimeError("partial model files left")

        step("install.cmd again: nothing to download, still OK")
        again = subprocess.run(
            ["cmd.exe", "/c", str(app / "install.cmd"), "-Shortcuts", "no", "-NoLaunch"],
            cwd=app,
            env=env,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        if again.returncode != 0:
            raise RuntimeError(f"second install.cmd failed with exit code {again.returncode}")

        step("start the installed app: models, speech, reading")
        smoke(app, env)

        step("uninstall.cmd -Yes -DeleteData")
        un = subprocess.run(
            ["cmd.exe", "/c", str(app / "uninstall.cmd"), "-Yes", "-DeleteData"],
            cwd=app,
            env=env,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        left = [p for p in (app / ".venv", app / ".tools", local / "EnglishTrainer") if p.exists()]
        if un.returncode != 0 or left:
            raise RuntimeError(f"uninstall left {left} (exit {un.returncode})")
        ok = True
    except Exception as e:
        print(f"clean install: FAILED: {e}", flush=True)
    finally:
        if args.keep:
            print(f"kept at {root}")
        else:
            shutil.rmtree(root, onexc=_force_remove)
    print(f"clean install: {'OK' if ok else 'FAILED'} in {time.monotonic() - started:.0f} s")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
