#!/usr/bin/env python
"""Render a theme to screencap.png without touching the physical panel.

Temporarily switches config.yaml to the simulated display, runs the monitor for
a few seconds, then always restores your config.

Defaults to PYTHON sensors so the clock and usage figures are REAL. Pass STATIC
as the third argument for frozen stub values (note: STATIC hardcodes the date to
2023-09-06 11:36, so the clock will look wrong - that is expected).

Usage:  venv/Scripts/python.exe preview-theme.py [ThemeName] [seconds] [PYTHON|STATIC]
"""
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.yaml"
BACKUP = ROOT / "config.yaml.preview-backup"

theme = sys.argv[1] if len(sys.argv) > 1 else "DeckWhiteBlue"
seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 12
sensors = sys.argv[3].upper() if len(sys.argv) > 3 else "PYTHON"


def patch(text):
    text = re.sub(r"^(\s*THEME:).*$", rf"\1 {theme}", text, flags=re.M)
    text = re.sub(r"^(\s*HW_SENSORS:).*$", rf"\1 {sensors}", text, flags=re.M)
    text = re.sub(r"^(\s*REVISION:).*$", r"\1 SIMU", text, flags=re.M)
    return text


def main():
    shutil.copy(CONFIG, BACKUP)
    proc = None
    try:
        CONFIG.write_text(patch(BACKUP.read_text(encoding="utf-8")), encoding="utf-8")
        shot = ROOT / "screencap.png"
        if shot.exists():
            shot.unlink()

        proc = subprocess.Popen(
            [sys.executable, "-u", "main.py"],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        time.sleep(seconds)
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
        out = ""
        if proc:
            try:
                out = proc.stdout.read() or ""
            except Exception:
                pass
        shutil.copy(BACKUP, CONFIG)
        BACKUP.unlink(missing_ok=True)

    interesting = [ln for ln in out.splitlines()
                   if not re.search(r"refresh done|Drawing |is now loaded in the cache", ln)]
    print("\n".join(interesting[:45]))
    print(f"\n--- screencap.png exists: {(ROOT / 'screencap.png').exists()} ---")


if __name__ == "__main__":
    main()
