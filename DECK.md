# Turing Deck

Background controller for the 3.5" panel: tray icon, global hotkeys, multiple
screens. No console window, starts at logon.

## Everyday use

| Action | How |
|---|---|
| Next screen | **Page Down** |
| Previous screen | **Page Up** |
| Pick a screen directly | Tray icon -> screen name |
| Flip the panel 180 degrees | Tray icon -> Flip display (a checkbox) |
| Restart the display | Tray icon -> Restart display |
| Stop everything | Tray icon -> Exit |
| Start it again | **Turing Deck** shortcut on the Desktop |

Screens:

- **Deck** - clock, date, uptime, CPU/GPU load and temperature, now playing.
- **Detail** - memory, disk, network down/up, GPU VRAM, ping.
- **Agenda** - weather now/today, next calendar event with a live countdown,
  and the two events after it.
- **Voice** - Discord voice channel: who is in it, who is talking right now,
  who just joined, and your own mute state.
- **League** - phase-driven: summoner spells and skill order once you lock in,
  starting items for the first 90 seconds, core build after that. Appears on
  its own at champion lock-in.
- **OTP** - a verification code that just arrived. Not in the cycle: it appears
  on its own and leaves on its own. See below.

The two deliberately do not overlap: clock and uptime live only on Deck, so the
Detail screen spends its space on things you cannot see anywhere else.

## Hotkeys are global

`PGUP`/`PGDN` are registered with `RegisterHotKey`, which claims them
**system-wide** - they no longer reach any other application, so Page Up/Down
will not scroll in browsers, editors or PDF readers. If that gets annoying,
edit `deck.yaml`:

    hotkeys:
      prev_screen: ALT+PGUP
      next_screen: ALT+PGDN

Then restart the deck. Modifiers: `CTRL`, `ALT`, `SHIFT`, `WIN`.

## Where the app lives

The app runs **from this repo**, using `venv\Scripts\pythonw.exe`. Edit a file
here and restart the deck - there is no deploy step.

It did not always. It used to be installed to `%LOCALAPPDATA%\TuringDeck` as a
self-contained folder carrying its own Python, via `tools\install_standalone.bat`.
That is still the better arrangement in principle - it is immune to anything you
do to the Python in your profile - but it cannot be maintained from a Claude Code
session on this machine, because `AppData\Local` is redirected into an app
container there. Installs appeared to succeed and ran fine, then vanished on the
first reboot with `0x80070002` from the logon task. See CLAUDE.md.

If you want the self-contained install back, **run the installer yourself** from
a normal elevated PowerShell (not through Claude):

    tools\install_standalone.bat

and remember that the config the app reads then becomes
`%LOCALAPPDATA%\TuringDeck\config.yaml`, not the one in this repo.

### Why not a single .exe

A PyInstaller build works (`turing-deck.spec`) but **will not run**: Smart App
Control blocks unsigned executables, and signing needs a paid certificate plus
reputation. A copied `python.exe` keeps its Authenticode signature from the
Python Software Foundation, so a self-contained folder is allowed where a
self-built exe is not. Do not spend time on the exe route again.

## Starting it by hand

Three ways, in order of convenience:

1. **Desktop shortcut "Turing Deck"** - runs the scheduled task, so it starts
   elevated with **no UAC prompt** and no window.
2. `tools
estart_deck.bat` - stops whatever is running and starts fresh. Use
   this after editing `deck.py` or `deck.yaml`; it only kills processes whose
   command line points at this repo, so other Python programs are safe.
3. `start-deck.bat` - direct launch, prompts for UAC.

Only one instance runs: the scheduled task is set to `IgnoreNew`, and the
supervisor terminates stray `main.py` processes on startup.

## Flip

**Tray icon -> Flip display** toggles `DISPLAY_REVERSE` in `config.yaml` and
restarts the display child. The restart is required because orientation is sent
once during display initialisation (`display.py` -> `SetOrientation`), not per
frame. The menu entry is a checkbox, so it always shows the current state.

## Agenda screen setup

Edit **`%LOCALAPPDATA%\TuringDeck\services.yaml`** (the installed copy - that is
what the app reads; it survives redeploys):

    weather:
      location: "Your City"     # geocoded once, then cached
      units: metric             # or imperial
    calendar:
      ics_url: ""               # private iCal URL, see below
      days_ahead: 7

Changes are picked up on the next poll (weather every 10 min, calendar every
5 min). For an immediate refresh use **tray icon -> Restart display**.

### Getting the calendar URL

Google Calendar -> Settings -> pick your calendar -> **"Secret address in iCal
format"**. Outlook: Calendar -> Share -> Publish -> ICS link.

This avoids the Google Cloud project, OAuth client and consent flow the Calendar
API would need - it is just a URL. **Treat it like a password**: anyone holding
it can read your calendar. `services.yaml` is git-ignored for that reason.

Two caveats worth knowing:

- Google regenerates nothing automatically, but it **caches the feed**, so a
  newly created event can take a while to appear. Existing event times are
  accurate; only freshly added ones lag.
- A local `.ics` file path works in place of a URL, which is handy for testing.

### Why Open-Meteo rather than the built-in weather

The project's own weather support uses OpenWeatherMap, which needs an account
and an API key. Open-Meteo needs neither, so the only thing you type is a city
name. It is fetched by `library/sensors/agenda.py`, not by the built-in
`WEATHER` stat, which stays unused.

## Voice screen setup (Discord)

Add to `%LOCALAPPDATA%\TuringDeck\services.yaml`:

    discord:
      client_id: "<Application ID>"
      client_secret: "<OAuth2 client secret>"

From **discord.com/developers/applications** -> your app. Two things matter:

1. **OAuth2 -> Redirects must contain `http://localhost`.** Without it every
   AUTHORIZE fails with `Missing "redirect_uri" in request`, whatever scopes you
   ask for. Do **not** send `redirect_uri` in the RPC request itself - Discord
   rejects that with "Redirect URI cannot be used in the RPC OAuth2
   Authorization flow". It must be registered, not transmitted.
2. `rpc.voice.read` is normally approval-gated, but an app's **owner and testers
   are exempt**, so your own app works on your own account with no application
   to Discord.

First run shows a Discord approval popup once; the token is cached in
`discord_token.json` and preserved across redeploys.

### What it shows

The big middle card is the focal point and never wastes space: whoever is
speaking, or failing that whoever just joined or left (for 45s), or "quiet".
The roster lists newest arrivals first, `+` for someone who joined in the last
two minutes and `*` for muted.

Note `GET_CHANNEL` does not report speaking state, so the roster refresh carries
it across - otherwise every refresh blanked whoever was mid-sentence.

## League screen

Between games the screen shows your **ranked profile** - tier, LP, W/L, ladder
position and your three most-played champions - instead of an empty "No match"
card. That needs your Riot ID in `services.yaml`:

```yaml
league:
  riot_id: "Name#TAG"
  meta_tier: emerald_plus     # bracket the champ-select meta list uses
```

Under that sits an **LP history graph** going back to the start of the season,
with the change over the last 7 and 30 days, and below it your most-played
champions.

It comes from dpm.lol's public JSON API - no key, no login, and the ID is
public information rather than a credential. Refreshed every 15 minutes and
cached on disk, so a failure keeps showing the last good profile.

Each segment of the line is drawn in the colour of the tier you were in at
that point - green through an emerald stretch, blue-purple through a diamond
one - so a promotion or a demotion is visible as a colour change rather than
needing a legend.

The graph plots dpm.lol's absolute ladder `score`, not LP: LP resets to 0 on
every promotion, so plotting it would draw a cliff downward at exactly the
moments you climbed. The y-axis is zoomed to the data rather than anchored at
zero, because a season's climb is a few hundred points out of several thousand
and would otherwise render flat.

In **champ select before you lock in** the screen shows the strongest
champions in your assigned role, from op.gg's tier list for `meta_tier`. Once
you lock in it switches to that champion's spells and runes as before.


Shows what is useful *right now*, changing with the phase of the game:

| Phase | Shows |
|---|---|
| **PICK** - locked in | Summoner spell pairs and rune pages |
| **START** - game start to 3:00 | Starting items, boots, and the skill order |
| **CORE** - after 3:00 | The five most-played core builds, ranked |
| **FINISH** - once 3 items are done | Fourth, fifth and sixth item options |
| No game | "No match" |

Every option carries its **win rate and sample size**, because 60% over 290
games and 56% over 8,605 games are not the same recommendation. Win rates at or
above 52% are coloured green.

The FINISH phase triggers on inventory, not the clock: three finished items,
counting cost and excluding boots and components.

It switches itself on at **lock-in**, not just at game start, so the spells and
skill order are already up when you need them. It switches back when the match
ends unless you moved screens by hand.

Deliberately absent: your level, CS and KDA. You can see those in the game.

### How this screen is drawn

Unlike the others, this one is a **single composited image**. The theme engine
can only drive text, bars and radials from a sensor, and this screen is
icon-led, so `library/sensors/league_render.py` draws the whole 480x320 frame
with PIL and returns a path, which a local `BITMAP` element in
`library/stats.py` blits.

That suits the panel too. A full frame costs ~1.3s at ~229 KB/s, but the picture
only changes when the phase changes - about three times a game - and the
filename carries a hash of the state, so an unchanged screen is never redrawn.
Icons are pulled from Data Dragon once and cached under `cache/icons/`.

The layout is a left rail plus a content pane, rather than the three stacked
cards the other screens use.

### Where the numbers come from

| Shown | Source | Notes |
|---|---|---|
| Champion, game time, inventory, enemy team | Live Client Data API on `127.0.0.1:2999` | Served by the game itself. No key, no rate limit, self-signed cert. The port only answers during a match, which is also the auto-switch signal. |
| Item costs, components, build trees | Riot Data Dragon | Cached per patch under `cache/`. |
| Which items, in what order | op.gg | One request per champion+role per day, cached on disk. |
| Spells, runes, starters, boots, core builds, 4th/5th/6th - each with win rate and games | op.gg | Same request, parsed per table row. Ability icons map to Q/W/E via Data Dragon's spell order, since op.gg's icon names do not encode the key. Rune art comes from op.gg's CDN - Data Dragon does not serve perks at a simple path. |
| Locked-in champion before the game loads | League client (LCU) via its lockfile | Only reports a *completed* pick - hovering a champion does not count. |
| Enemy AD/AP split | Computed | Each champion's innate attack/magic rating plus the damage actually in their inventory, so the read sharpens as the game goes on. |

### Why op.gg is scraped rather than called

Riot publishes no build recommendations: Data Dragon's `recommended` block and
Community Dragon's `recommendedItemDefaults` are both empty (verified, not
assumed). None of the stats sites offer a public API. op.gg renders its build
pages server-side, so the item ids are already in the HTML - a plain request
gets them, with no browser and no bot challenge involved. `stats2.u.gg` answers
403 behind Cloudflare and is deliberately left alone.

This is an unofficial source and may change shape or start refusing us. Every
failure falls back to a stale cache, then to `league_builds.yaml`, so the screen
keeps working either way - watch for `LOCAL` instead of `OP.GG` in the top right,
which is the tell that the scrape stopped working.

### Fixed rules worth knowing

- **Starter items are skipped after 8 minutes.** Otherwise a missing Doran's
  Bow keeps claiming the card at 19 minutes, pushing the useful item off it.
- **Consumables and trinkets are filtered out** of op.gg's starter list, which
  otherwise makes "Health Potion" your build path.
- **Champion names are indexed twice**, under the Data Dragon key and the
  display name: the live game says `Kai'Sa` and `Wukong` where Data Dragon says
  `Kaisa` and `MonkeyKing`. op.gg wants `monkeyking` and `renata`.
- For supports op.gg lists the support item under *Boots* and only a potion as
  the starter, so support build paths begin at boots.
- **The page uses three different stat layouts** and assuming one silently drops
  whole sections: core/starters/boots read `pick% / games / win%`, summoner
  spells omit the `%` on the pick rate, and 4th/5th/6th give `win% / games` with
  no pick rate at all.
- The op.gg cache is versioned (`CACHE_VERSION`). Changing the payload shape
  without bumping it leaves yesterday's cache in the old format and the screen
  dies on a missing key.

### Checking and changing builds

```
venv/Scripts/python.exe tools/check_league_builds.py   # validate names vs patch
venv/Scripts/python.exe tools/league_selftest.py       # run the logic, no game
TURING_LEAGUE_DEMO=1 ... preview-theme.py DeckLeague   # render a fake match
```

To override a build, put it in `services.yaml` under `league: builds:` - that
file is preserved across reinstalls, `league_builds.yaml` is not. Setting
`league: use_opgg: false` disables the network entirely and uses the local file.

## OTP screen setup

When a one-time passcode arrives, the panel shows who sent it and the code, for
30 seconds, then puts back whatever screen was there. **Page Up** or **Page
Down** dismisses it early and returns you where you were rather than cycling
onward - either key, because when a code is up neither one means "next screen".

Off until you configure a source. Everything lives in `services.yaml` under
`otp:`; `deck.yaml` only holds the screen name and whether it is enabled.

### Email

Gmail needs an **App Password**, not your account password:

1. Turn on 2-Step Verification on the Google account.
2. Create an app password at <https://myaccount.google.com/apppasswords>.
3. Put the 16-character value in `services.yaml`.

You can watch **as many mailboxes as you like**. Each entry under `accounts:`
gets its own thread and its own IMAP connection; keys set directly under
`email:` are shared defaults that every account inherits and may override:

```yaml
otp:
  email:
    enabled: true
    host: imap.gmail.com      # shared by every account below
    port: 993
    folder: INBOX
    accounts:
      - label: personal
        user: "you@gmail.com"
        app_password: "xxxx xxxx xxxx xxxx"
      - label: work
        user: "you@company.com"
        host: outlook.office365.com   # overrides the shared default
        app_password: "yyyy yyyy yyyy yyyy"
```

`label` is optional and appears on the panel next to EMAIL, so you can tell
which inbox a code landed in. Omit it if you only watch one. A single mailbox
can also be configured flat, with no `accounts:` list at all.

The same code arriving in two watched inboxes interrupts the panel **once** -
deduplication is shared across every source, mail and notifications alike.

Treat each app password like a password: it grants read access to that mailbox.
It is only ever sent to that account's `host`, and nothing here sends mail.
Other providers work; set `host` and `port` to their IMAP endpoint.

Measured cost, in the supervisor process (no extra process is created):

| Part | RSS |
|---|---|
| 3 mailboxes on IMAP IDLE | +5.3 MB (~1.8 MB each) |
| Toast notification watcher | +13.6 MB |

The toast watcher is the expensive half - it pulls in the WinRT projection and
an asyncio loop - and costs more than three mailboxes combined. Turn it off
under `otp.notifications` if you only care about email.

Check every configured mailbox without waiting for a real code - it logs in,
selects the folder and reports IDLE support, reading no messages:

```bash
venv/Scripts/python.exe tools/otp_selftest.py --imap
```

Every mailbox is checked even after one fails, so a single bad password does not
hide a second one behind it.

Delivery uses IMAP **IDLE**. Expect a code on the panel within ~30s of the
mail arriving: Gmail batches IDLE notifications by about 25 seconds
(measured), and the screen switch adds ~2s. If the server refuses IDLE the
watcher falls back to polling every `poll_seconds` and says so in `deck.log`.

### Windows notifications

`otp.notifications.enabled` also scores Windows toasts, which is how a phone
code arrives - Phone Link mirrors the message as a toast and it gets read like
any other. Windows asks once for notification access.

Verified working from an unpackaged Python process on this machine: the listener
returns `Allowed`, and each toast carries its package family name, so filtering
by source app is exact rather than title matching. Add app names or package
families to `ignore_apps` to mute a noisy source.

### How a code is recognised

Not a bare six-digit regex - that matches order numbers, prices, years and
tracking IDs. Candidates are scored and the best one has to clear a threshold:

| Signal | Weight |
|---|---|
| Intent phrase within 45 characters ("verification code", "one-time password") | +5 |
| ...within 120 characters | +3 |
| Weak word nearby - covers "482913 is your Instagram code", where no phrase forms | +2 |
| Found in the subject line | +2 |
| Exactly six digits | +2 |
| Sender looks like `no-reply@` / `security@` | +1 |
| An order/invoice/tracking/promo word within 30 characters | -4 |
| Looks like a year | -6 |
| Currency symbol before it, or part of a grouped number | -5 |

Two rules do most of the work in practice:

- **Only arrivals are ever scored.** Nothing scans a mailbox, so the corpus is
  "what landed in the last few minutes", which excludes almost all archive noise.
  Mail older than `max_age_seconds` is ignored, so a reconnect that hands over a
  backlog cannot replay yesterday's codes.
- **Ambiguity shows nothing.** If the two best candidates differ and score within
  2 of each other, the panel stays put. A missed code costs a glance at your
  phone; a confidently wrong one costs a failed login.

HTML mail is stripped of `<style>` blocks and URLs before scoring, because hex
colours (`#4419af`) and tracking query strings both parse as valid six-character
codes and would otherwise outnumber the real one.

To see the scoring on your own mail shapes:

```bash
venv/Scripts/python.exe tools/otp_selftest.py
```

```bash
venv/Scripts/python.exe tools/otp_selftest.py --text "Your code is 419022"
```

If something is missed or a false positive gets through, the weights are all in
`library/sensors/otp.py` and `tools/otp_selftest.py` prints the reason each
candidate scored what it did.

### Worth knowing

A code on a desk panel is readable by anyone in the room, and by any camera,
screen share or stream pointed that way. Thirty seconds is short by design; set
`hold_seconds` lower if that matters more than convenience.

The IMAP connection lives in the **supervisor**, not the display child. The
child is killed and respawned on every screen switch, so a connection held there
would reconnect to Gmail every time you pressed Page Down. The supervisor writes
`cache/otp_state.json` and the child reads it - which is also why the code
survives the very switch that puts it on screen.

## Switching cost

A switch kills the display child and starts a new one. Measured end to end:

| Phase | Time |
|---|---|
| Terminate child | ~30 ms |
| COM port settle (`com_settle_seconds`) | 50 ms |
| Python start + imports | ~400 ms |
| Transmit the 480x320 background | ~1310 ms |

The last row dominates and is not fixable in software: the panel runs at about
229 KB/s, and a full RGB565 frame is 307,200 bytes. Prediction 1.31 s, logged
flush 1.4 s.

The settle used to be a guessed 0.6 s - a quarter of every switch. Measured with
`tools/measure_com_release.py`, the port is actually reusable **2.9 ms** after
the owner exits (n=10, max 3.1 ms), so it is now 50 ms. Kill-to-spawn went from
607 ms to 55 ms, with no port-open failures over repeated switches.

Restarting a process per screen costs ~400 ms of interpreter startup. Switching
themes in-process would reclaim that, but `scheduler.py` reads its intervals
from `THEME_DATA` at import time and its `STOPPING` flag is one-way, so it means
`importlib.reload()` gymnastics - roughly 400 ms saved out of ~1.8 s, in
exchange for losing crash isolation. Not worth it.

Idle cost of the whole thing: ~127 MB RAM and 0.04% of a 12-core CPU.

## Adding a screen

1. Create `res/themes/<Name>/theme.yaml` (copy an existing Deck theme).
2. Add its background to `tools/make_deck_backgrounds.py` and re-run it.
3. Add an entry to `deck.yaml` under `screens:`.
4. Restart the deck.

Preview any theme without touching the panel:

    venv/Scripts/python.exe preview-theme.py <ThemeName> 12

## How it works

`deck.py` owns the tray icon and hotkeys and runs `main.py` as a **child
process**. Switching a screen rewrites `THEME` in `config.yaml` and restarts
that child.

Why not switch themes in-process: `scheduler.py` evaluates its `@schedule(...)`
decorators at import time, reading refresh intervals straight from `THEME_DATA`,
and its `STOPPING` flag is one-way. Live swapping would need `importlib.reload()`
gymnastics. Restarting a child is simpler and isolates crashes - a broken screen
cannot take down the controller. The panel holds its last frame during the
~1 second swap, so it reads as a brief freeze, not a black flash.

Other behaviours:

- **Stray cleanup**: on startup the supervisor terminates any `main.py` running
  outside its control. Only one process can hold COM3.
- **Watchdog**: if the child dies on its own it is restarted, with exponential
  backoff (2s -> 60s) so a screen that crashes at startup cannot spin.
- **Child tray icon suppressed** via `DECK_CHILD=1`, so there is one icon.
- Logs to `deck.log`; the display logs to `log.log`.

## Elevation and autostart

Elevation matters for exactly one thing: CPU temperature comes from AMD's Ryzen
Master SDK CLI, which refuses to run without admin. Everything else works fine
unelevated.

There are two autostart arrangements, and it is worth knowing which you have:

| | Registered by | UAC at logon | CPU temp |
|---|---|---|---|
| `Turing Deck` (highest run level) | `tools\install_autostart.bat`, **run as admin** | none | works |
| `Turing Deck Autostart` (limited) | fallback, no admin needed | none | **n/a** |

The elevated one is preferred. Register it from an elevated PowerShell:

    tools\install_autostart.bat

Both target `pythonw.exe` directly rather than a `.bat`, so no console flashes,
and both delay ~20s for USB to enumerate.

    # which tasks exist
    Get-ScheduledTask -TaskName "Turing Deck*"
    # remove one
    Unregister-ScheduledTask -TaskName "Turing Deck Autostart" -Confirm:$false

The Desktop shortcut runs `start-deck.bat`, which self-elevates - so it always
gives you the elevated variant, at the cost of one UAC prompt.

## Troubleshooting

`start-monitor.bat` runs a single screen **with a visible console**, which is the
quickest way to see errors - `deck.py` swallows them into `deck.log` by design.
Exit the deck from its tray icon first, or the COM port will be busy.

**OTP screen stopped appearing?** Check `deck.log` for the mail watchers.
Occasional `reconnecting in 5s` lines are normal - the network drops idle
connections and the watcher resumes from where it stopped, so nothing is
lost. Two failure shapes have actually happened and are guarded against:

- `cannot read from timed out object`: IDLE was once ended by a socket read
  timing out, which poisons an SSL connection. `idle_wait()` must end IDLE by
  sending `DONE`, never by letting the read expire.
- Codes silently missing with a clean log: this network kills idle TCP
  connections inside 14 minutes, and a dead connection never receives the
  EXISTS. That is why `IDLE_REFRESH` is 240s and TCP keepalives run every
  30s - do not "optimise" the refresh back up toward the RFC's 29 minutes.

Expect a code to take up to ~30s to appear even when everything is healthy:
Gmail batches IDLE notifications by about 25 seconds, which is a floor no
change on our side can lower.

**Panel unplugged?** Nothing to do. The child fails to open COM3, and the
watchdog retries with backoff up to 60s, so reconnecting the panel brings the
display back on its own within a minute. `deck.log` will show
`Cannot open COM port COM3` until then.

**Running previews while the deck is running:** `preview-theme.py` temporarily
rewrites `config.yaml`, and so does a screen switch. Exit the deck first if you
are iterating on a theme, otherwise a respawning child can pick up the
preview's simulated-display setting.
