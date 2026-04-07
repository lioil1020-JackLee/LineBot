from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run_pyinstaller(spec_name: str) -> int:
    root = _project_root()
    spec_path = root / spec_name
    if not spec_path.exists():
        print(f"Spec not found: {spec_path}")
        return 1

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(spec_path),
        "--noconfirm",
    ]
    result = subprocess.run(cmd, cwd=root, check=False)
    return int(result.returncode)


def _clean_outputs() -> None:
    root = _project_root()
    for name in ("build", "dist", ".pyinstaller"):
        path = root / name
        if path.exists():
            shutil.rmtree(path)
            print(f"Removed: {path}")


def build_onedir() -> None:
    raise SystemExit(_run_pyinstaller("linebot-onedir.spec"))


def build_onefile() -> None:
    raise SystemExit(_run_pyinstaller("linebot-onefile.spec"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build LineBot package with PyInstaller")
    parser.add_argument("--mode", choices=["onedir", "onefile"], required=True)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    if args.clean:
        _clean_outputs()

    spec = "linebot-onedir.spec" if args.mode == "onedir" else "linebot-onefile.spec"
    raise SystemExit(_run_pyinstaller(spec))


if __name__ == "__main__":
    main()
