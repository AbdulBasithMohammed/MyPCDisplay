# Custom sensors

Everything in a theme's `STATS: CUSTOM:` block resolves to a class **by name** in
`sensors_custom.py`. The theme names `LeagueFrame`, the loop does
`getattr(sensors_custom, "LeagueFrame")()`, and calls it. That indirection is the
whole contract, and it has sharp edges.

## The four rules

**1. All state must be class-level.** The render loop constructs a *fresh sensor
object every tick*. Anything stored on `self` is thrown away immediately. Pollers,
caches and marquee offsets all live as class attributes.

**2. Never raise.** An exception is caught and logged per-sensor, and that element
silently disappears. Wrap anything that touches IO, parsing or the network.

**3. Never return an empty string.** `DisplayText` asserts on empty text. Return
a dash, a status word, or the last good value — never `""`.

**4. Never block.** The loop is single-threaded and shared with rendering. Any IO
belongs on a daemon thread that publishes to class state; sensors only read that
state. `Discord`, `League` and `_AmdTempPoller` all follow this shape:

```python
class Thing:
    _started = False
    _lock = threading.Lock()
    value = None                      # what sensors read

    @classmethod
    def ensure_started(cls):
        with cls._lock:
            if cls._started:
                return
            cls._started = True
            threading.Thread(target=cls._loop, daemon=True).start()
```

Sensors call `ensure_started()` on every tick; it is cheap after the first.

## Exports

`sensors_custom.py` does `from library.sensors.x_sensors import *`, and the theme
looks names up in *that* namespace. Without an `__all__`, a wildcard import also
exports the state classes and helpers you imported — `League`, `Static`,
`_marquee` — into the sensor namespace. Declare `__all__` listing only the sensor
classes.

## Element types a sensor can drive

| Key in theme.yaml | Fed by | Notes |
|---|---|---|
| `TEXT` | `as_string()` | |
| `GRAPH` | `as_numeric()` | progress bar |
| `RADIAL` | both | `as_string()` becomes the centre label |
| `LINE_GRAPH` | `last_values()` | |
| `BITMAP` | `as_string()` returning a **file path** | **local addition**, see below |

`BITMAP` is not upstream. It exists because the theme engine cannot otherwise
draw an image from a sensor, and the League screen is icon-led. The sensor
returns a path; `display_themed_bitmap` in `library/stats.py` blits it and skips
the redraw when the path and mtime are unchanged — mandatory, because a
full-screen blit costs ~1.3 s on this panel.

## Files

| File | Role |
|---|---|
| `sensors_custom.py` | Base sensors + the wildcard imports that register the rest |
| `agenda.py` / `agenda_sensors.py` | Weather (Open-Meteo) and calendar (iCal) |
| `discord_rpc.py` / `discord_sensors.py` | Discord voice state over the local IPC pipe |
| `league.py` | Match state, phases, Data Dragon, champ select |
| `opgg.py` | Scrapes and parses op.gg build pages |
| `league_render.py` | Composites the whole League frame with PIL |
| `league_sensors.py` | Exposes that frame as one `BITMAP` sensor |

## Traps that have already bitten

- **`import re` was missing** in `stats.py` and the guard that was supposed to add
  it matched `import requests`. Every custom sensor silently went blank. When
  patching a file by string match, verify the result afterwards.
- **Wildcard export leaked `League`** into the sensor namespace until `__all__`
  was added.
- **A cached payload outlived its schema.** `opgg.py` gained new fields, the
  24-hour disk cache kept serving the old shape, and the screen died on a missing
  key. `CACHE_VERSION` now invalidates it — **bump it whenever the payload shape
  changes**.
- **`GET_CHANNEL` does not report speaking state**, so refreshing the Discord
  roster blanked whoever was mid-sentence. It is carried across refreshes.
- **Champion names need two keys.** The live game says `Kai'Sa` and `Wukong`;
  Data Dragon keys them `Kaisa` and `MonkeyKing`; op.gg wants `monkeyking`.
  `Static.champs` is indexed under both, and `SLUG_ALIASES` handles op.gg.
- **op.gg uses three different stat layouts** and assuming one silently drops
  whole sections rather than erroring:
  `pick% / games / win%` (core, starters, boots), `pick / games / win%`
  (summoner spells, no `%` on pick), `win% / games` (4th/5th/6th, no pick).

## Adding a sensor

1. Write the class in the relevant `*_sensors.py`, honouring the four rules.
2. Add it to that module's `__all__`.
3. Reference it by class name in the theme's `STATS: CUSTOM:` block.
4. Preview it — no hardware needed:

```bash
venv/Scripts/python.exe preview-theme.py <ThemeName> 15
```

If the element does not appear, it almost always means the sensor raised or
returned empty. Check `log.log` for `Error loading custom sensor class`.
