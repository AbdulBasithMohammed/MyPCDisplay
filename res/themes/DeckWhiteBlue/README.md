# DeckWhiteBlue

White + light blue landscape theme (480x320) for the Turing Smart Screen rev A.

## Layout

Three cards, geometry defined in both `make_background.py` and `theme.yaml` —
**change both together or the text will drift off its card.**

| Card | Box (x0,y0,x1,y1) | Contents |
|------|-------------------|----------|
| A | 10, 8, 470, 98 | `HH:mm` clock, date + uptime (right-aligned), seconds bar |
| B | 10, 106, 470, 230 | CPU / GPU usage radials, temperature text + heat bars |
| C | 10, 238, 470, 312 | Now Playing title + artist |

Radial `X`/`Y` are the **centre** of the circle, not the top-left.
Text `X`/`Y` are top-left; right-alignment needs `ANCHOR: rt` (setting
`ALIGN: right` alone does nothing for single-line text).

## Palette

Edit the constants at the top of `make_background.py`, re-run it, and update the
matching RGB values in `theme.yaml`.

- accent `45, 164, 232` · navy text `18, 52, 80` · muted `110, 144, 168`
- gauge track `223, 240, 252` · card edge `201, 227, 246`

Regenerate the background:

    venv/Scripts/python.exe res/themes/DeckWhiteBlue/make_background.py

Preview without the panel (renders to `screencap.png`, restores your config):

    venv/Scripts/python.exe preview-theme.py DeckWhiteBlue 12

## Fonts

Now Playing uses **Malgun Gothic** (`res/fonts/malgun/`), copied from
`C:\Windows\Fonts`. The bundled fonts cover Latin only — real track titles
contain Hangul, CJK and emoji. Malgun covers everything except emoji, which
`sensors_custom.py` strips. Do not redistribute this theme with the font.

## Local modifications to upstream code

Search for `LOCAL MODIFICATION` in each file. **A `git pull` may conflict with
or revert these.**

1. `library/stats.py` - adds `COLOR_THRESHOLDS` (text) and
   `BAR_COLOR_THRESHOLDS` (bars); used here to turn temperatures amber above
   70 C and red above 83 C.
2. `library/stats.py` - treats a CPU temperature of 0 as unavailable, not as a
   real reading (see "CPU temperature reads 0" below).
3. `library/stats.py` - `_thresholded_color` also parses a leading number out
   of a formatted string, so `COLOR_THRESHOLDS` works for custom sensors that
   return e.g. `"42°C"`.
4. `library/stats.py` - the custom-sensor loop uses `continue` instead of
   `return` on error. Upstream aborts the entire loop on one bad sensor, which
   silently blanks every custom element after it.
5. `library/lcd/lcd_comm.py` - fixes radial inner text centring. Callers
   right-justify values to `min_size`, so the text arrives padded (`"  8%"`);
   PIL's `getbbox()` counts those spaces, so centring the padded string pushed
   the digits right by half the padding - 12px at 8%, 6px at 41%, 0px at 100%,
   which looked like the number wobbling as the value changed. Now centres on
   the visible glyphs.

## CPU temperature: read via the AMD Ryzen Master SDK

`HW_SENSORS: PYTHON`, and CPU temperature comes from a custom sensor, not the
built-in `CPU/TEMPERATURE` stat (which is set `SHOW: False`). Reason:

LibreHardwareMonitor reads AMD CPU sensors through the ring0 driver WinRing0.
That driver is on Microsoft's vulnerable-driver blocklist, and this machine runs
Memory Integrity (HVCI) with `VulnerableDriverBlocklistEnable = 1`, so Windows
refuses to load it - no WinRing0 service is registered at all. Without ring0
access LHM reports `0.0` for CPU temperature, package power and per-core clocks.
psutil has no temperature support on Windows either.

This is why the GPU was always correct: NVIDIA values come from NVML, a normal
driver API, untouched by the blocklist.

The workaround: **AMD's own `AMDRyzenMasterDriver.sys` is signed and loads fine
under HVCI.** `_AmdTempPoller` in `sensors_custom.py` shells out to the SDK's
prebuilt CLI:

    AMDRyzenMasterCLI.exe --api GetPMTableData
    -> "cHTC Current Value: 41.783680 celsius"

Notes:

- **Requires elevation.** Without it the CLI does not fail fast, it *hangs* -
  hence the `IsUserAnAdmin()` pre-check before ever spawning it.
  `start-monitor.bat` self-elevates.
- Each call costs ~1.2s, so it polls every 10s on a daemon thread, off the
  render path, with a 60s backoff after repeated failures.
- Only read-only `Get*` APIs are used. The CLI can also *set* overclocking
  values - do not call those.
- `GetCurrentTemperature` is deprecated; `GetPMTableData` is the current API.
- MSI Afterburner is **not** an alternative - it uses `RTCore64.sys`, also
  blocklisted. HWiNFO would work but its free version cuts Shared Memory off
  after 12 hours per session.

If the SDK is missing or elevation is absent, the sensor renders `n/a` in muted
grey. `run_diag.bat` dumps what LHM can see, for comparison.

## Gotchas learned the hard way

- `DisplayText` asserts on empty strings, and one raising custom sensor aborts
  *all* the others — so the Now Playing sensors never return `""` and never raise.
- Custom sensors are re-instantiated every tick, so scroll state is class-level.
- Setting both `WIDTH` and `HEIGHT` on a text element fixes the erase rectangle,
  which is what stops variable-length text from ghosting.
