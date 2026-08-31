# Turing Deck

A 3.5" USB panel on my desk, running as a always-on dashboard: PC health, a
clock, calendar and weather, Discord voice state, and a League of Legends build
guide that follows the phase of the match.

Built on [turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)
by mathoudebine — the upstream README is kept as [UPSTREAM_README.md](UPSTREAM_README.md),
and the upstream licence and authors are unchanged. Everything described
below is local work on top of that project.

---

## The hardware

| | |
|---|---|
| Panel | "TURMO" 3.5" Turing Smart Screen, **revision A** |
| USB id | `USB\VID_1A86&PID_5722\USB35INCHIPSV2`, sub-revision `USBMONITOR_3_5` |
| Port | `COM3` |
| Resolution | 480×320, landscape |
| Real throughput | **~229 KB/s** — a full frame is 307,200 bytes, so ~1.3 s |

That last number sets the ceiling for everything. Video is roughly 31× out of
reach; partial redraws are the only way to feel responsive.

## The screens

Cycle with **Page Down** / **Page Up**, or pick one from the tray icon.

| Screen | Shows |
|---|---|
| **Deck** | Clock, date, uptime, CPU/GPU load and temperature, now playing |
| **Detail** | Memory, disk, network up/down, GPU VRAM, ping |
| **Agenda** | Weather now/today, next calendar event with a live countdown, two after it |
| **Voice** | Discord voice channel: who is in it, who is talking, who just joined |
| **League** | Rank profile between games, then a phase-driven build guide — see below |
| **OTP** | A one-time passcode that just arrived — appears on its own, then leaves |

The **League** screen changes with the state of the match, and switches itself on
at champion lock-in:

| Phase | Shows |
|---|---|
| **Between games** | Rank, LP, W/L, ladder position and your most-played champions |
| **Champ select** — before lock-in | The strongest champions in your assigned role right now |
| **PICK** — locked in | Summoner spell pairs and rune pages |
| **START** — 0:00 to 1:30 | Starting items, boots, skill order |
| **CORE** — after 3:00 | The five most-played core builds, ranked |
| **FINISH** — 3 items done | Fourth / fifth / sixth item options |

Every option carries its **win rate and sample size**, because 60% over 290 games
and 56% over 8,605 games are not the same recommendation.

The **OTP** screen is not in the cycle. When a verification code arrives by
email — or in a Windows notification, which is how a phone code gets here via
Phone Link — the panel shows who sent it and the code itself for 30 seconds,
then puts back whatever was there. **Page Up** or **Page Down** dismisses it
early. Any number of mailboxes can be watched at once, each labelled so you
can tell which inbox a code landed in. It is off until you add credentials;
see [DECK.md](DECK.md#otp-screen-setup).

Codes are matched by scoring, not by a bare six-digit regex: an intent phrase
has to sit near the number, order numbers and prices score negative, and two
equally plausible candidates show nothing rather than a guess.

## Running it

**Desktop shortcut** — `Turing Deck` starts it (accepts a UAC prompt, which is
what lets the CPU temperature sensor work).

**At logon** — a scheduled task starts it automatically. See
[Autostart](#autostart) for the two variants and why elevation matters.

**By hand**

```bat
start-deck.bat
```

Stop it from the tray icon → **Exit**.

## First-time setup

Two things are deliberately **not** in this repo and have to be supplied locally.

**1. Your settings and secrets**

```bash
cp services.example.yaml services.yaml
```

Then fill in your weather location, private iCal URL and Discord application
credentials. `services.yaml` is git-ignored — it holds a calendar URL that lets
anyone read your calendar, and a Discord client secret.

**2. Malgun Gothic**

The themes use Malgun Gothic for its CJK and symbol coverage. It ships with
Windows and is licensed with it, so it cannot be redistributed here. Copy it
from your own machine:

```bat
mkdir res\fonts\malgun
copy %WINDIR%\Fonts\malgun*.ttf res\fonts\malgun\
```

Without it the Deck, Agenda and Voice screens fall back to a default face and
non-Latin text will not render.

## Configuration

| File | Holds | Committed? |
|---|---|---|
| `config.yaml` | COM port, revision, brightness, and runtime state the app writes back (current theme, flip) | yes |
| `deck.yaml` | Which screens exist and their order, hotkeys, auto-switch, `com_settle_seconds` | yes |
| `services.yaml` | **Secrets** — private calendar URL, Discord client id/secret, weather location | **no**, git-ignored |
| `league_builds.yaml` | Offline fallback build orders, used only when op.gg is unreachable | yes |

`services.yaml` in the repo is an empty template. Fill in your own; it is
git-ignored precisely because it holds a calendar URL that anyone can read your
calendar with, and a Discord client secret.

## Autostart

Two variants, and the difference matters:

- **Elevated (preferred).** `tools/install_autostart.bat`, run as administrator,
  registers a logon task at highest run level. No UAC prompt at logon, and the
  CPU temperature sensor works because it can reach AMD's Ryzen Master CLI.
- **Non-elevated fallback.** A task named `Turing Deck Autostart` at
  `RunLevel Limited`. Starts silently but cannot read CPU temperature.

To check which you have:

```bash
schtasks /Query /TN "Turing Deck" /V /FO LIST
```

## Development

```bash
venv/Scripts/python.exe preview-theme.py DeckLeague 15
```

Renders a theme to `screencap.png` without touching the panel. Set
`TURING_LEAGUE_DEMO=1` to populate the League screen with a fabricated match.

Other tools worth knowing:

| Command | Does |
|---|---|
| `tools/league_selftest.py` | Drives every League phase against fabricated match data — no game needed |
| `tools/check_league_builds.py` | Validates every item name in `league_builds.yaml` against the live patch |
| `tools/make_deck_backgrounds.py` | Regenerates every theme background from one palette |
| `tools/benchmark_panel.py` | Measures real panel throughput (stop the deck first) |
| `tools/measure_com_release.py` | Measures how fast COM3 frees up after the owner exits |

## Known constraints on this machine

These are settled findings, not open questions — see [DECK.md](DECK.md) for the
evidence behind each.

- **Memory Integrity (HVCI) blocks WinRing0**, so LibreHardwareMonitor reads
  0.0 for CPU temperature, package power and clocks. CPU temperature comes from
  AMD's Ryzen Master SDK CLI instead, which needs elevation.
- **Smart App Control blocks unsigned executables**, so the PyInstaller build
  (`turing-deck.spec`) produces a binary Windows refuses to launch. The app runs
  from source instead.
- **Riot publishes no build recommendations.** Data Dragon's `recommended` block
  and Community Dragon's `recommendedItemDefaults` are both empty. op.gg is
  scraped from its server-rendered HTML; `stats2.u.gg` sits behind a Cloudflare
  challenge and is left alone.

## Where to read more

- **[DECK.md](DECK.md)** — the deep reference: every screen, where each number
  comes from, the fixed rules, and troubleshooting.
- **[CLAUDE.md](CLAUDE.md)** — orientation for working on this with Claude Code,
  including the environment traps that have already cost real debugging time.
- **[UPSTREAM_README.md](UPSTREAM_README.md)** — the original project's docs.
