# Working on this repo

A fork of [turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)
driving a 3.5" Turing panel (revision A, COM3, 480×320) as a desk dashboard.
[README.md](README.md) is the overview, [DECK.md](DECK.md) the deep reference.
This file is the things that will waste your time if you do not know them.

---

## Environment traps

### `%LOCALAPPDATA%` is redirected — installs there do not reach the real disk

Claude Code runs in a packaged (MSIX) container here. `AppData\Local` is
redirected into
`C:\Users\Admin\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\`.

This silently broke the app for a full day. `tools/install_standalone.ps1`
appeared to succeed every time, and the deck ran fine — **because it was launched
from inside the container**. After a reboot the logon task, which Task Scheduler
runs *outside* the container, got `0x80070002` FILE_NOT_FOUND and nothing
started.

**Comparing file timestamps across the two paths does not detect this** — both
resolve to the same file, so they look identical. That comparison was made, and
led to the wrong conclusion that the paths were the same folder. Use a marker:

```bash
echo x > /c/Users/Admin/AppData/Local/TuringDeck/__t.txt
find /c/Users/Admin/AppData/Local/Packages/Claude_*/LocalCache -name __t.txt
```

What is **real and writable**: the repo, `Desktop`, `C:\Users\Admin\*`, and
`AppData\Roaming` (so the Startup folder works). Only `AppData\Local` is
redirected.

**Consequence: the deck runs from the repo, not from an install directory.**
Do not "fix" this by reinstating a `%LOCALAPPDATA%` install unless the user runs
the installer themselves.

### What a Claude Code shell cannot do here

| Action | Result |
|---|---|
| Stop the running deck | works *if* it started from `Turing Deck Autostart` (Limited); **access denied** if from `Turing Deck` (Highest) — check `Win32_Process.CommandLine`, null means elevated |
| Register a task at `RunLevel Highest` | **Access denied** |
| Write real `%LOCALAPPDATA%` | Silently redirected |
| Register a task at `RunLevel Limited` | works |
| Write HKCU `Run`, Startup folder, Desktop | works |

`Get-Process | ... $_.Path` returns **empty** for elevated processes rather than
erroring, and `$p.Handle` can succeed even when you cannot terminate the
process. Neither is a valid elevation test — try `Stop-Process -ErrorAction Stop`
and read the error, or check whether `Win32_Process.CommandLine` is null.

Anything needing elevation must be handed to the user as a command, or run via
`Start-Process -Verb RunAs` (which raises a UAC prompt they must accept — do not
do that while they are in a game).

---

## Local modifications to upstream files

These are edits inside files that came from upstream, so **a `git pull` can
revert them**. Each is marked `LOCAL MODIFICATION` in-place. If a screen suddenly
goes blank or a value reads wrong after syncing upstream, check here first.

| File | Change | Why |
|---|---|---|
| `library/stats.py` | `_thresholded_color` parses numbers out of strings | Upstream assumes a float; custom sensors return text like `"41°C"` |
| `library/stats.py` | CPU temp guard is `isnan(t) or t <= 0` | LHM returns 0.0 when the driver is blocked; 0 °C is not a reading |
| `library/stats.py` | custom-sensor loop uses `continue`, not `return` | Upstream aborts the whole loop on one bad sensor, blanking every element after it |
| `library/stats.py` | `BITMAP` element + `display_themed_bitmap` | Lets a sensor drive an image; the League screen composites its own frame |
| `library/lcd/lcd_comm.py` | radial text centres on `text.strip()` | `min_size` padding was counted by `getbbox`, so percentages sat off-centre |
| `main.py` | `DECK_CHILD=1` suppresses the tray icon | The supervisor owns the tray; the child must not create a second one |

---

## Architecture

```
deck.py            tray icon, global hotkeys, supervises main.py as a child
  ├── main.py      upstream render loop; reads THEME from config.yaml
  │     └── library/sensors/sensors_custom.py   custom sensors (see its CLAUDE.md)
  └── library/sensors/otp_sources.py            IMAP IDLE + toast watchers

tools/amd_temp_service.py   separate ELEVATED task ("Turing CPU Temp"),
                            writes cache/cpu_temp.json; nothing imports it
```

**The OTP watchers live in the supervisor on purpose.** The child dies on every
screen switch, so an IMAP connection held there would reconnect to Gmail each
time you pressed PgDn. The supervisor writes `cache/otp_state.json`; the child
reads it. That handoff is also what lets the code survive the switch that
displays it.

Switching screens **rewrites `THEME` in config.yaml and restarts the child**. It
looks heavy-handed, and it is deliberate: `scheduler.py` reads refresh intervals
from `THEME_DATA` at *import* time and its `STOPPING` flag is one-way, so
swapping themes in-process needs `importlib.reload()` gymnastics. Restarting a
child is simpler and isolates crashes.

**Do not "optimise" this into in-process switching.** It was measured: the
restart costs ~400 ms of interpreter startup out of a ~1.8 s switch. The
dominant cost is ~1.3 s transmitting the background over a 229 KB/s serial link,
which no architecture change touches.

### Switch cost, measured

| Phase | Time |
|---|---|
| Terminate child | ~30 ms |
| COM settle (`com_settle_seconds`) | 50 ms |
| Python start + imports | ~400 ms |
| Transmit 480×320 background | ~1310 ms |

The settle was a guessed 600 ms — a quarter of every switch — until
`tools/measure_com_release.py` showed the port is reusable **2.9 ms** after the
owner exits (n=10, max 3.1 ms). Kill-to-spawn went 607 ms → 55 ms.

---

## Testing without the hardware

Nothing here needs the panel plugged in.

```bash
venv/Scripts/python.exe preview-theme.py DeckLeague 15   # renders screencap.png
venv/Scripts/python.exe tools/league_selftest.py         # every League phase
venv/Scripts/python.exe tools/check_league_builds.py     # item names vs live patch
venv/Scripts/python.exe tools/otp_selftest.py            # OTP scoring, with reasons
venv/Scripts/python.exe -m pytest tests/library/sensors/ -q
```

`TURING_OTP_DEMO=1` fills the OTP screen with a fabricated code, the same way
`TURING_LEAGUE_DEMO=1` works for League.

**Give `preview-theme.py` at least ~5 seconds.** A shorter run can exit before
the first custom-stat pass completes and renders a background with every sensor
element blank - which looks exactly like a sensor crash and is not one.

`TURING_LEAGUE_DEMO=1` fills the League screen with a fabricated match, so the
preview shows real content instead of "No match".

The League screen also has live introspection worth using before guessing:

```bash
venv/Scripts/python.exe -c "from library.sensors.league import gameflow_phase, champ_select; print(gameflow_phase(), champ_select())"
```

---

## Settled findings — do not re-investigate

Each of these cost real time. They are conclusions, not guesses.

- **LibreHardwareMonitor cannot read this CPU.** WinRing0 is on Microsoft's
  vulnerable-driver blocklist and HVCI is on, so LHM returns 0.0 — *not* NaN,
  which is why the "sensor missing" warning never fires. The user has said
  Memory Integrity stays on.
- **CPU temperature needs a separate elevated process — do not put it back in
  the render loop.** The Ryzen Master CLI is the only source of die temperature
  here and it refuses to run unelevated (`User is not admin...`, exit 0). The
  old `_AmdTempPoller` spawned it in-process behind an `IsUserAnAdmin()` check,
  so when the deck started from `Turing Deck Autostart` (RunLevel **Limited**)
  the poller thread returned on its first line and the panel read `n/a` forever
  — across restarts and reboots, because nothing retried and the WARNING was
  logged once per process. 56 of those lines sat in `log.log` over four days
  before anyone noticed. `tools/amd_temp_service.py` now runs as the elevated
  task `Turing CPU Temp` and publishes `cache/cpu_temp.json`; the sensor reads
  the file and reports staleness. Elevating the whole deck instead would put the
  IMAP client and the op.gg/dpm.lol parsers under an admin token — see
  [DECK.md](DECK.md#elevation-and-autostart).
- **There is no unelevated CPU temperature source on this machine.** Verified,
  not assumed: `MSAcpi_ThermalZoneTemperature` answers `Not supported`, and
  `Win32_PerfFormattedData_Counters_ThermalZoneInformation` does not exist. Do
  not go looking again.
- **PyInstaller output will not launch.** Smart App Control blocks unsigned
  binaries. `turing-deck.spec` is kept only as a record. The user has said Smart
  App Control stays on.
- **No free API gives League build recommendations.** Data Dragon `recommended`
  and Community Dragon `recommendedItemDefaults` are both empty; both verified.
  Riot's Match API needs a key that expires every 24 h.
- **`stats2.u.gg` answers 403 behind a Cloudflare challenge.** Do not attempt to
  work around bot protection. op.gg serves the same data in server-rendered HTML
  with no challenge, so that is what is parsed.
- **`tmp` in the repo root is a FILE, not a directory.**
  `library/lcd/lcd_simulated.py` saves the simulated panel to a file with that
  exact name on every render, and `.gitignore` lists it as such. `os.makedirs`
  on `tmp/` therefore fails with `FileExistsError` the moment anyone has run
  `preview-theme.py`. Per-machine runtime state goes in `cache/`.
- **Reading Windows toasts from unpackaged Python works.** `UserNotificationListener`
  returns `Allowed`, not `Denied`, and exposes `package_family_name` for exact
  source filtering (Phone Link is `Microsoft.YourPhone_8wekyb3d8bbwe`). The
  namespaces are separate pip packages - `winrt-Windows.UI.Notifications` does
  **not** pull in `.Management`, and `AppInfo` needs `winrt-Windows.ApplicationModel`.
  All four are pinned in `requirements.txt`.
- **Never end an IMAP IDLE by letting the socket read time out.** A timed-out
  read on an `SSLSocket` poisons the connection: every subsequent read raises
  `cannot read from timed out object`. The first version used
  `sock.settimeout(refresh)` as its refresh mechanism, so all three mail
  watchers tore down and rebuilt every 840s, and any code arriving during a
  rebuild was silently missed — the panel looked like it had simply stopped
  working. `idle_wait()` now ends IDLE by sending `DONE` from a timer thread
  and reads on until the tagged completion line; the socket timeout is only a
  dead-link net at `refresh + 60`. Soak-tested at a 5s refresh: 39 cycles
  across three live connections, zero reconnects.
- **A reconnecting mail watcher must resume from the last processed UID**, not
  re-read the mailbox tip. Re-reading the tip discards anything that arrived
  while the connection was down. `max_age_seconds` is what stops a resumed
  watcher acting on stale mail.
- **This network silently kills idle TCP connections in under 14 minutes.**
  Measured, not inferred: deck.log 2026-08-26 22:47-22:48 shows one IMAP
  connection reset (`WinError 10054`) at the exact moment its 840s IDLE timer
  sent DONE, and a second blackholed so completely that only the read timeout
  noticed - the EXISTS for a message APPENDed mid-idle never arrived, while an
  identical fresh connection received it in 24s. Fixing the SSL-timeout bug
  *exposed* this: the old bug's teardown-every-cycle had been an accidental
  NAT keepalive. Defenses now: `IDLE_REFRESH = 240` (each DONE/IDLE/SEARCH
  cycle moves bytes both ways), TCP keepalives every 30s
  (`SIO_KEEPALIVE_VALS`), and the session loop scans *before* idling so a
  reconnect catches the backlog immediately. Do not raise `IDLE_REFRESH` back
  toward the RFC's 29 minutes: the constraint is the NAT, not the RFC.
- **Gmail batches IDLE notifications by ~25s.** An APPEND (and plausibly some
  deliveries) reached an idling connection 24s after the fact (measured with
  `scratchpad/idle_trace.py`-style raw tracing). Detection latency has this
  floor regardless of anything on our side.
- **`imaplib` has no `IDLE` support on Python 3.13.** `IMAP4.idle()` landed
  later, so `otp_sources.py` drives the protocol directly and degrades to
  polling on any failure rather than reconnect-looping.
- **op.gg has no JSON API.** It is Next.js server components; the ids are in the
  page HTML. A plain `requests` call is enough — no browser at runtime.

---

## Conventions

- Comments explain **why**, not what. Several comments here exist purely to stop
  a future change from re-introducing a bug that has already been fixed once.
- Match the surrounding style: this codebase uses `%`-formatting and plain
  `try/except` in sensor code because it runs inside a render loop that must
  never raise.
- When a fix is based on a measurement, **put the number in the comment**
  (`com_settle_seconds`, the throughput figures). It stops the next person
  re-deriving it, and flags when it stops being true.
- Verify before asserting. Several bugs in this project's history came from
  inferring success rather than checking — a silently-empty parse section, an
  install that "succeeded" into a sandbox, a driver assumed loaded. Run the
  check.
