"""Elevated CPU-temperature reader. Publishes cache/cpu_temp.json.

Why this exists as a separate process
-------------------------------------
AMD's Ryzen Master SDK CLI is the only thing on this machine that can read the
CPU die temperature: LibreHardwareMonitor needs WinRing0, which Memory
Integrity blocks, and psutil has no temperature support on Windows. The CLI
refuses to run without admin - it prints "User is not admin..." and exits.

That leaves one privileged requirement in an otherwise unprivileged app. The
obvious fix - run the whole deck elevated - would put the IMAP client, the
op.gg/dpm.lol scrapers and the PIL decoding of downloaded art in an admin
token. Those parse untrusted network data; one of them has already shipped a
path-traversal bug once. So the privilege is isolated here instead: this
process takes no arguments, reads no configuration, and executes exactly one
hardcoded path.

Data flows one way, elevated -> unelevated: this writes the JSON, the deck only
reads it. A user-level process tampering with the file can therefore only make
the panel show a wrong number, which it could already do by editing the theme.
Nothing here ever reads that file back.

Registered as the scheduled task "Turing CPU Temp" by tools/install_temp_service.bat.
Run it in a console to watch it work:

    venv/Scripts/python.exe tools/amd_temp_service.py --once
"""
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(ROOT, "cache", "cpu_temp.json")

# Hardcoded on purpose: this process runs elevated, so the command it spawns
# must not be influenced by anything a user-level process can write.
CLI = os.path.join(
    os.environ.get("ProgramFiles", "C:" + os.sep + "Program Files"),
    "AMD", "RyzenMasterSDK", "AMDRyzenMasterCLI", "bin-prebuilt",
    "AMDRyzenMasterCLI.exe",
)

POLL_SECONDS = 30       # each call spawns a ~1.2s process; die temps move slowly
FAIL_SECONDS = 60       # back off when it is not working, so a broken setup stays cheap
CALL_TIMEOUT = 20

# e.g. "GetPMTableData ... cHTC Current Value: 41.783680 celsius"
TEMP_RE = re.compile(r"cHTC Current Value\s*:\s*([0-9]+(?:\.[0-9]+)?)")


def is_admin():
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def read_once():
    """Return (celsius, reason). celsius is None when the read failed."""
    if not os.path.exists(CLI):
        return None, "Ryzen Master SDK not installed"
    try:
        out = subprocess.run(
            [CLI, "--api", "GetPMTableData"],
            cwd=os.path.dirname(CLI),
            capture_output=True, text=True, timeout=CALL_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout or ""
    except subprocess.TimeoutExpired:
        return None, "CLI timed out after %ds" % CALL_TIMEOUT
    except Exception as e:
        return None, type(e).__name__

    low = out.lower()
    if "not admin" in low:
        return None, "needs admin"
    if "platform init failed" in low:
        # The SDK cannot reach its driver. The driver showing as Running is not
        # enough - it reaches this state and stays there until the service is
        # restarted or the machine reboots. Deliberately not self-healed: the
        # user has asked that drivers not be stopped from here.
        return None, "SDK: platform init failed (restart AMDRyzenMasterDriverV29)"

    m = TEMP_RE.search(out)
    if not m:
        first = (out.strip().splitlines() or ["empty output"])[0][:60]
        return None, "unexpected CLI output: " + first
    return float(m.group(1)), ""


def publish(celsius, reason):
    """Write the reading atomically, so a reader never sees a half-written file."""
    payload = {"celsius": celsius, "reason": reason, "ts": time.time(),
               "poll_seconds": POLL_SECONDS}
    tmp = STATE_FILE + ".tmp"
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, STATE_FILE)
        return True
    except Exception as e:
        # Never silent: a swallowed write error here looks exactly like a dead
        # sensor from the panel's side, and that has cost a day before.
        sys.stderr.write("cpu_temp publish failed: %s: %s\n" % (type(e).__name__, e))
        return False


def main():
    once = "--once" in sys.argv
    if not is_admin():
        sys.stderr.write(
            "not elevated - the Ryzen Master CLI will refuse to read.\n"
            "Install this as a task with tools/install_temp_service.bat\n")
        publish(None, "temp service not elevated")
        if once:
            return 1
    while True:
        celsius, reason = read_once()
        publish(celsius, reason)
        if once:
            print("celsius=%s reason=%s -> %s" % (celsius, reason or "ok", STATE_FILE))
            return 0 if celsius is not None else 1
        time.sleep(POLL_SECONDS if celsius is not None else FAIL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
