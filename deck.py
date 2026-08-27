#!/usr/bin/env python
"""Background controller for the Turing smart screen.

Owns the tray icon and global hotkeys, and supervises main.py as a child
process. Switching screens rewrites THEME in config.yaml and restarts the child.

Why a supervisor instead of switching themes in-process: scheduler.py evaluates
its @schedule(...) decorators at import time, reading refresh intervals straight
out of THEME_DATA, and its STOPPING flag is one-way. Swapping themes live would
mean importlib.reload() gymnastics; restarting a child is simpler and isolates
crashes. The panel holds its last frame while the child restarts, so a switch
reads as a brief freeze rather than a black flash.

Run with pythonw.exe so there is no console window.
"""
import ctypes
import logging
import os
import re
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import pystray
import yaml
from PIL import Image

# Frozen (PyInstaller one-folder) puts res/, config.yaml and the sibling
# TuringDisplay.exe next to the executable. Running from source, everything is
# next to this file and the display is launched through the venv interpreter.
FROZEN = getattr(sys, "frozen", False)
ROOT = Path(sys.executable if FROZEN else __file__).resolve().parent
CONFIG = ROOT / "config.yaml"
DECK_CFG = ROOT / "deck.yaml"
PYTHONW = ROOT / "venv" / "Scripts" / "pythonw.exe"
DISPLAY_EXE = ROOT / "TuringDisplay.exe"
ICON = ROOT / "res" / "icons" / "monitor-icon-17865" / "64.png"

# Pause between the display child exiting and the next one claiming the COM
# port. Windows closes the handle on exit, but the next open can race it.
#
# This was a guessed 0.6s, which was a quarter of every screen switch. Measured
# on this hardware (tools/measure_com_release.py, n=10): the port is reusable
# 2.9ms after the owner exits, max 3.1ms. 50ms is a 16x margin on the worst
# case. Raise it via `com_settle_seconds` in deck.yaml if a machine ever fails
# to open the port on switch - the symptom is "Cannot open COM port" in log.log
# followed by a watchdog restart.
COM_SETTLE = 0.05

logging.basicConfig(
    filename=str(ROOT / "deck.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("deck")

# --------------------------------------------------------------------------
# Win32 global hotkeys
# --------------------------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
MODS = {"ALT": 0x0001, "CTRL": 0x0002, "CONTROL": 0x0002, "SHIFT": 0x0004, "WIN": 0x0008}
VKEYS = {
    "PGUP": 0x21, "PRIOR": 0x21, "PGDN": 0x22, "NEXT": 0x22,
    "END": 0x23, "HOME": 0x24,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "INSERT": 0x2D, "DELETE": 0x2E, "SPACE": 0x20,
}
for _i in range(1, 25):
    VKEYS["F" + str(_i)] = 0x6F + _i
for _c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    VKEYS[_c] = ord(_c)


def parse_hotkey(spec):
    """Turn "ALT+PGDN" into (modifiers, virtual_key). None if unparseable."""
    parts = [p.strip().upper() for p in str(spec).split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    for p in parts[:-1]:
        if p not in MODS:
            log.error("Unknown modifier %r in hotkey %r", p, spec)
            return None
        mods |= MODS[p]
    key = parts[-1]
    if key not in VKEYS:
        log.error("Unknown key %r in hotkey %r", key, spec)
        return None
    return mods | MOD_NOREPEAT, VKEYS[key]


def hotkey_pump(bindings, on_trigger):
    """Register hotkeys and pump messages.

    Must run on its own thread: WM_HOTKEY is delivered to the thread that
    registered the hotkey, so registration and the message loop must share one.
    """
    ids = {}
    for i, (action, spec) in enumerate(bindings.items(), start=1):
        parsed = parse_hotkey(spec)
        if parsed is None:
            continue
        mods, vk = parsed
        if user32.RegisterHotKey(None, i, mods, vk):
            ids[i] = action
            log.info("Registered hotkey %s -> %s", spec, action)
        else:
            # Usually means another application already owns this combination.
            log.error("Could not register hotkey %s (error %d) - already taken?",
                      spec, ctypes.get_last_error())
    if not ids:
        log.warning("No hotkeys registered")
        return
    msg = wintypes.MSG()
    while True:
        r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if r in (0, -1):
            break
        if msg.message == WM_HOTKEY:
            action = ids.get(msg.wParam)
            if action:
                try:
                    on_trigger(action)
                except Exception:
                    log.exception("Hotkey handler failed")


# --------------------------------------------------------------------------
# Supervisor
# --------------------------------------------------------------------------
class Supervisor:
    def __init__(self, screens):
        self.screens = screens
        self.index = 0
        self.proc = None
        self.lock = threading.RLock()
        self.switching = False
        self._stop = False

    def current_name(self):
        return self.screens[self.index]["name"]

    def _set_theme(self, theme):
        text = CONFIG.read_text(encoding="utf-8")
        new, n = re.subn(r"^(\s*THEME:).*$", r"\1 " + theme, text, flags=re.M)
        if n != 1:
            log.error("Expected one THEME line in config.yaml, found %d", n)
            return
        CONFIG.write_text(new, encoding="utf-8")

    @staticmethod
    def get_reverse():
        """True when the panel is rendering upside down (DISPLAY_REVERSE)."""
        m = re.search(r"^\s*DISPLAY_REVERSE:\s*(\S+)",
                      CONFIG.read_text(encoding="utf-8"), flags=re.M)
        return bool(m) and m.group(1).strip().lower() in ("true", "yes", "1")

    @staticmethod
    def _set_reverse(value):
        text = CONFIG.read_text(encoding="utf-8")
        new, n = re.subn(r"^(\s*DISPLAY_REVERSE:).*$",
                         r"\1 " + ("true" if value else "false"), text, flags=re.M)
        if n != 1:
            log.error("Expected one DISPLAY_REVERSE line in config.yaml, found %d", n)
            return False
        CONFIG.write_text(new, encoding="utf-8")
        return True

    def toggle_flip(self):
        """Flip the panel 180 degrees. Needs a child restart: orientation is sent
        once during display initialisation, not per frame."""
        with self.lock:
            new_value = not self.get_reverse()
            if not self._set_reverse(new_value):
                return
            log.info("Flip display -> %s", "reversed" if new_value else "normal")
            self.restart()

    def restart(self):
        with self.lock:
            self.switching = True
            try:
                self._kill()
                self._spawn()
            finally:
                self.switching = False

    def _spawn(self):
        env = dict(os.environ)
        env["DECK_CHILD"] = "1"          # tells main.py not to create a tray icon
        env["PYTHONIOENCODING"] = "utf-8"
        # sys.executable is whichever pythonw is running us, so this works both
        # from the repo venv and from the self-contained install, with no
        # hard-coded interpreter path to go stale.
        cmd = [str(DISPLAY_EXE)] if FROZEN else [sys.executable, "-u", "main.py"]
        self.proc = subprocess.Popen(
            cmd, cwd=str(ROOT), env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log.info("Started monitor pid=%s screen=%s", self.proc.pid, self.current_name())

    def _kill(self):
        if not self.proc or self.proc.poll() is not None:
            self.proc = None
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        log.info("Stopped monitor")
        self.proc = None
        time.sleep(COM_SETTLE)

    @staticmethod
    def kill_strays():
        """Terminate any main.py started outside this supervisor.

        Only one process can hold the COM port; a leftover instance makes every
        spawn here fail with PermissionError on the port.
        """
        try:
            import psutil
        except Exception:
            return
        me = os.getpid()
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if p.info["pid"] == me:
                    continue
                name = (p.info["name"] or "").lower()
                cmd = " ".join(p.info["cmdline"] or []).lower()
                is_display = (name == "turingdisplay.exe"
                              or (name.startswith("python") and "main.py" in cmd))
                if not is_display:
                    continue
                if str(ROOT).lower() not in (p.cwd() or "").lower():
                    continue
                log.info("Terminating stray monitor pid=%s", p.info["pid"])
                p.terminate()
                p.wait(timeout=8)
            except Exception:
                continue
        time.sleep(0.6)

    def start(self):
        with self.lock:
            self.kill_strays()
            self._set_theme(self.screens[self.index]["theme"])
            self._spawn()

    def switch_to(self, index):
        index %= len(self.screens)
        with self.lock:
            self.switching = True
            try:
                self.index = index
                self._kill()
                self._set_theme(self.screens[index]["theme"])
                self._spawn()
            finally:
                self.switching = False

    def visible_indices(self):
        """Screens the user can reach by cycling.

        The OTP screen is marked hidden in deck.yaml: it is pushed onto the
        panel when a code arrives and taken away again, so having it in the
        rotation would mean cycling onto a stale code.
        """
        return [i for i, s in enumerate(self.screens) if not s.get("hidden")]

    def cycle(self, delta):
        visible = self.visible_indices()
        if not visible:
            return
        if self.index in visible:
            target = visible[(visible.index(self.index) + delta) % len(visible)]
        else:
            # Cycling off a hidden screen: enter the rotation from whichever
            # end the direction implies.
            target = visible[0] if delta > 0 else visible[-1]
        self.switch_to(target)

    def shutdown(self):
        self._stop = True
        with self.lock:
            self._kill()

    def watch(self):
        """Restart the child if it dies on its own, backing off so a screen that
        crashes at startup cannot turn into a spin loop."""
        backoff = 2
        while not self._stop:
            time.sleep(2)
            with self.lock:
                if self._stop or self.switching or self.proc is None:
                    continue
                if self.proc.poll() is None:
                    backoff = 2
                    continue
                log.error("Monitor exited unexpectedly (code %s); restarting in %ds",
                          self.proc.returncode, backoff)
                self.proc = None
            time.sleep(backoff)
            with self.lock:
                if not self._stop and not self.switching and self.proc is None:
                    self._spawn()
            backoff = min(backoff * 2, 60)


class Notifier:
    """Pushes the OTP screen onto the panel, then puts back what was there.

    Deliberately not modelled on league_watch: that polls a state that lasts
    for minutes, while this reacts to an instant and has to time out on its
    own. What it does borrow is the rule that the panel is yours - if you
    changed screens by hand while a code was up, the timer expires quietly
    instead of yanking you somewhere.
    """

    def __init__(self, sup, screen_index, hold_seconds, icon=None):
        self.sup = sup
        self.screen_index = screen_index
        self.hold = hold_seconds
        self.icon = icon
        self._lock = threading.Lock()
        self._return_to = None
        self._deadline = 0.0

    def show(self, code, sender, source, label=""):
        from library.sensors import otp

        otp.publish(code, sender, source, self.hold, label)
        with self._lock:
            already_up = self.sup.index == self.screen_index
            if not already_up:
                self._return_to = self.sup.index
            # A second code arriving while the first is up restarts the clock
            # rather than stacking, so the newer code gets its full 30s.
            self._deadline = time.time() + self.hold
        if not already_up:
            log.info("OTP from %s; showing notification screen", sender)
            self.sup.switch_to(self.screen_index)
            if self.icon:
                self.icon.update_menu()

    def dismiss(self):
        """Early dismissal.

        Returns True only when the panel was actually showing the code, since
        the hotkey uses that to decide whether it has handled the keypress. If
        you had already moved to another screen, the pending timer is cancelled
        but False comes back so PgUp/PgDn still cycles as normal - swallowing
        the key there would look like a dead hotkey.
        """
        from library.sensors import otp

        with self._lock:
            if self._deadline <= 0:
                return False
            on_panel = self.sup.index == self.screen_index
            target, self._return_to, self._deadline = self._return_to, None, 0.0
        otp.clear()
        if not on_panel:
            return False
        if target is not None:
            log.info("OTP dismissed; returning to %s", self.sup.screens[target]["name"])
            self.sup.switch_to(target)
            if self.icon:
                self.icon.update_menu()
        return True

    def run(self):
        from library.sensors import otp

        while not self.sup._stop:
            time.sleep(0.5)
            with self._lock:
                if self._deadline <= 0 or time.time() < self._deadline:
                    continue
                target, self._return_to, self._deadline = self._return_to, None, 0.0
            otp.clear()
            if self.sup.index != self.screen_index:
                continue          # you moved on already; leave you where you are
            if target is not None:
                log.info("OTP expired; returning to %s",
                         self.sup.screens[target]["name"])
                self.sup.switch_to(target)
                if self.icon:
                    self.icon.update_menu()


def otp_watch(sup, screen_name, icon=None):
    """Start the OTP sources and wire them to the notification screen."""
    target = next((i for i, s in enumerate(sup.screens)
                   if s["name"].lower() == str(screen_name).lower()), None)
    if target is None:
        log.warning("OTP: no screen named %r in deck.yaml; disabled", screen_name)
        return None

    try:
        from library.sensors import otp, otp_sources
    except Exception:
        log.exception("OTP sensors unavailable; disabled")
        return None

    services = otp.load_services().get("otp") or {}
    if not services.get("enabled", True):
        log.info("OTP screen disabled in services.yaml")
        return None

    hold = float(services.get("hold_seconds") or 30)
    notifier = Notifier(sup, target, hold, icon)
    otp.clear()               # a code left over from a previous run is stale

    if not otp_sources.start(services, notifier.show):
        return None

    threading.Thread(target=notifier.run, name="otp-timer", daemon=True).start()
    return notifier


def league_watch(sup, screen_name, return_after, icon=None):
    """Jump to the League screen at champion lock-in, and back when the game ends.

    "Busy" comes from the client's own gameflow phase, which spans champ select,
    the loading screen and the match as one state. An earlier version watched
    champ select and the in-game API separately and bounced the panel back to
    the previous screen the instant champ select closed, because neither API
    answers during loading.

    If you switch screens by hand during a match we leave you there: the return
    only fires when the League screen is still the one on the panel.
    """
    target = next((i for i, s in enumerate(sup.screens)
                   if s["name"].lower() == str(screen_name).lower()), None)
    if target is None:
        log.warning("League auto-switch: no screen named %r in deck.yaml", screen_name)
        return

    # The client's gameflow phase covers the whole lifecycle in one call.
    # Watching champ select and the in-game API separately does not work: they
    # are different APIs with the loading screen in between, so the screen would
    # flip away right as the match was starting.
    try:
        from library.sensors.league import league_busy
    except Exception:
        log.warning("League sensors unavailable; auto-switch disabled")
        return

    was_live, previous, misses = False, None, 0

    while not sup._stop:
        time.sleep(5)
        try:
            live = league_busy()
        except Exception:
            live = False

        # Require two consecutive misses before leaving, so one dropped request
        # to the client cannot bounce the panel off mid-game.
        if live:
            misses = 0
        else:
            misses += 1
            if was_live and misses < 2:
                continue

        if live and not was_live:
            previous = sup.index
            if sup.index != target:
                log.info("Match started; switching to %s", sup.screens[target]["name"])
                sup.switch_to(target)
                if icon:
                    icon.update_menu()
        elif not live and was_live:
            if return_after and previous is not None and sup.index == target:
                log.info("Match ended; returning to %s", sup.screens[previous]["name"])
                sup.switch_to(previous)
                if icon:
                    icon.update_menu()
            previous = None
        was_live = live


def load_cfg():
    cfg = yaml.safe_load(DECK_CFG.read_text(encoding="utf-8")) or {}
    screens = cfg.get("screens") or []
    missing = [s for s in screens
               if not (ROOT / "res" / "themes" / str(s.get("theme"))).is_dir()]
    for s in missing:
        log.error("Skipping screen %r: theme folder %r not found", s.get("name"), s.get("theme"))
    screens = [s for s in screens if s not in missing]
    if not screens:
        raise SystemExit("deck.yaml lists no screens whose theme folder exists")
    return cfg, screens


def main():
    global COM_SETTLE
    cfg, screens = load_cfg()
    try:
        COM_SETTLE = max(0.0, float(cfg.get("com_settle_seconds", COM_SETTLE)))
    except (TypeError, ValueError):
        log.warning("com_settle_seconds is not a number; keeping %.3fs", COM_SETTLE)
    sup = Supervisor(screens)
    sup.index = max(0, min(int(cfg.get("start_screen", 0) or 0), len(screens) - 1))
    if screens[sup.index].get("hidden"):
        # start_screen pointing at the OTP screen would leave the panel
        # showing an empty notification until something arrived.
        visible = sup.visible_indices()
        log.warning("start_screen %d is a hidden screen; starting on %r",
                    sup.index, screens[visible[0]]["name"])
        sup.index = visible[0]

    def make_select(i):
        def handler(icon_, item_):
            sup.switch_to(i)
            icon_.update_menu()
        return handler

    def is_current(i):
        return lambda item_: sup.index == i

    def on_next(icon_, item_):
        sup.cycle(+1)
        icon_.update_menu()

    def on_restart(icon_, item_):
        sup.restart()

    def on_flip(icon_, item_):
        sup.toggle_flip()
        icon_.update_menu()

    def on_exit(icon_, item_):
        sup.shutdown()
        icon_.stop()

    items = [pystray.MenuItem(s["name"], make_select(i), checked=is_current(i), radio=True)
             for i, s in enumerate(screens) if not s.get("hidden")]
    items += [
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Next screen", on_next),
        pystray.MenuItem("Flip display", on_flip,
                         checked=lambda item_: Supervisor.get_reverse()),
        pystray.MenuItem("Restart display", on_restart),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", on_exit),
    ]

    icon = pystray.Icon(name="Turing Deck", title="Turing Deck",
                        icon=Image.open(ICON), menu=pystray.Menu(*items))

    # Assigned once otp_watch has run; None when OTP is off.
    notifier = [None]

    def on_hotkey(action):
        if action not in ("next_screen", "prev_screen"):
            return
        # While a code is on the panel, either key means "I have read it" -
        # dismissing back to where you were beats cycling onward, which would
        # leave you on a screen you did not ask for.
        if notifier[0] is not None and notifier[0].dismiss():
            return
        sup.cycle(+1 if action == "next_screen" else -1)
        icon.update_menu()

    sup.start()
    threading.Thread(target=sup.watch, name="watchdog", daemon=True).start()
    threading.Thread(target=hotkey_pump, args=(cfg.get("hotkeys") or {}, on_hotkey),
                     name="hotkeys", daemon=True).start()

    otp_cfg = cfg.get("otp") or {}
    if otp_cfg.get("enabled", True):
        notifier[0] = otp_watch(sup, otp_cfg.get("screen", "OTP"), icon)

    auto = cfg.get("auto_switch") or {}
    if auto.get("enabled", True) and auto.get("on_league_match", True):
        threading.Thread(target=league_watch,
                         args=(sup, auto.get("screen", "League"),
                               auto.get("return_after", True), icon),
                         name="league-watch", daemon=True).start()

    try:
        icon.run()            # blocks on the main thread until Exit is chosen
    finally:
        sup.shutdown()
        log.info("Deck stopped")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Deck crashed")
        raise
