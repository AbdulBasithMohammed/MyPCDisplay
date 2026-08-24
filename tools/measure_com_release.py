#!/usr/bin/env python
"""How long after a process exits can the next one open COM3?

deck.py sleeps a fixed 0.6s between killing the display child and starting the
next one, on the theory that Windows needs a moment to release the handle. This
measures whether that is true, and what the number actually is.

Models the real sequence: a child holds the port, gets terminated, and we time
how long until a fresh open succeeds.
"""
import statistics
import subprocess
import sys
import time

import serial

PORT = "COM3"
TRIALS = 10
HOLDER = ("import serial,time;"
          "s=serial.Serial('%s',115200);"
          "print('held',flush=True);"
          "time.sleep(120)" % PORT)


def port_free():
    try:
        serial.Serial(PORT, 115200).close()
        return True
    except Exception:
        return False


def main():
    if not port_free():
        print("COM3 is busy - stop the deck first")
        return 1

    waits = []
    for trial in range(1, TRIALS + 1):
        p = subprocess.Popen([sys.executable, "-c", HOLDER],
                             stdout=subprocess.PIPE, text=True)
        # Wait until the holder actually owns the port.
        p.stdout.readline()
        deadline = time.time() + 5
        while port_free() and time.time() < deadline:
            time.sleep(0.01)

        p.terminate()
        p.wait(timeout=10)

        # The clock starts the instant the owner is gone, exactly like _kill.
        t0 = time.perf_counter()
        elapsed = None
        while time.perf_counter() - t0 < 5:
            if port_free():
                elapsed = (time.perf_counter() - t0) * 1000
                break
            time.sleep(0.002)
        waits.append(elapsed)
        print("  trial %2d: %s" % (trial, "%.1f ms" % elapsed if elapsed is not None
                                   else "STILL BUSY after 5s"))

    ok = [w for w in waits if w is not None]
    print()
    if ok:
        print("  n=%d  min %.1f  median %.1f  mean %.1f  max %.1f ms"
              % (len(ok), min(ok), statistics.median(ok), statistics.mean(ok), max(ok)))
    if len(ok) != len(waits):
        print("  %d trial(s) never released within 5s" % (len(waits) - len(ok)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
