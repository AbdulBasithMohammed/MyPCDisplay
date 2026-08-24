#!/usr/bin/env python
"""Measures real throughput of the panel: how fast can we actually push pixels?

Answers "could this show video". Run with the deck stopped - only one process
can hold the COM port.
"""
import io
import sys
import time

sys.path.insert(0, ".")

from PIL import Image
from library.lcd.lcd_comm_rev_a import LcdCommRevA, Orientation

PORT = "COM3"
W, H = 320, 480          # portrait native; landscape is set below

out = io.StringIO()


def say(msg):
    print(msg)
    out.write(msg + "\n")


def noise(w, h, seed):
    """A different image every frame, so nothing can be skipped or cached."""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            c = ((x * 7 + y * 13 + seed * 31) % 256, (x * 3 + seed) % 256, (y * 5 + seed) % 256)
            for dy in range(4):
                for dx in range(4):
                    if x + dx < w and y + dy < h:
                        px[x + dx, y + dy] = c
    return img


def bench(lcd, w, h, frames, label):
    imgs = [noise(w, h, i) for i in range(min(frames, 4))]
    t0 = time.perf_counter()
    for i in range(frames):
        lcd.DisplayPILImage(imgs[i % len(imgs)], 0, 0)
    lcd.WaitForPendingRequests() if hasattr(lcd, "WaitForPendingRequests") else None
    dt = time.perf_counter() - t0
    per = dt / frames
    px = w * h
    kbs = (px * 2 * frames) / dt / 1024        # RGB565 = 2 bytes/pixel
    say(f"{label:<22} {w:>3}x{h:<3}  {per*1000:>7.0f} ms/frame   {1/per:>6.2f} fps   {kbs:>6.0f} KB/s")
    return per


def main():
    say("connecting...")
    lcd = LcdCommRevA(com_port=PORT, display_width=W, display_height=H)
    lcd.InitializeComm()
    lcd.SetBrightness(level=20)
    lcd.SetOrientation(orientation=Orientation.LANDSCAPE)
    say(f"connected: {lcd.get_width()}x{lcd.get_height()} landscape")
    say("")
    say("area                    size      per frame        rate    throughput")
    say("-" * 72)

    bench(lcd, 480, 320, 5, "full screen")
    bench(lcd, 240, 160, 10, "quarter screen")
    bench(lcd, 160, 120, 15, "small region")
    bench(lcd, 96, 96, 20, "icon sized")
    bench(lcd, 200, 40, 25, "text line")

    say("")
    lcd.closeSerial()
    with open("benchmark_result.txt", "w", encoding="utf-8") as f:
        f.write(out.getvalue())


if __name__ == "__main__":
    main()
