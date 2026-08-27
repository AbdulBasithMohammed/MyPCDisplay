#!/usr/bin/env python
"""Sources that feed OTP detection: IMAP mail, and Windows toast notifications.

Both run as daemon threads inside the *supervisor*, not the display child. The
child is killed and respawned on every screen switch, so a connection held
there would be torn down and re-established each time - which for IMAP means
reconnecting to Gmail every time you press PgDn.

Detection itself lives in otp.py; this file only obtains text and hands it over.
"""
import asyncio
import email
import email.utils
import logging
import re
import threading
import time

from library.sensors import otp

log = logging.getLogger(__name__)

# Do not show the same code twice within this window. Mail and toast sources
# can both see the same OTP - a phone mirroring an email notification, say -
# and without this the panel would fire twice for one code.
DEDUPE_SECONDS = 300


class Dispatcher:
    """Deduplicates codes from all sources and forwards them once."""

    def __init__(self, on_otp):
        self._on_otp = on_otp
        self._recent = {}
        self._lock = threading.Lock()

    def offer(self, code, sender, source, label=""):
        now = time.time()
        with self._lock:
            for old, seen_at in list(self._recent.items()):
                if now - seen_at > DEDUPE_SECONDS:
                    del self._recent[old]
            if code in self._recent:
                log.debug("OTP %s from %s suppressed as duplicate", code, source)
                return False
            self._recent[code] = now
        log.info("OTP detected via %s%s from %r", source,
                 " [%s]" % label if label else "", sender)
        try:
            self._on_otp(code, sender, source, label)
        except Exception:
            log.exception("OTP handler failed")
        return True


# ---------------------------------------------------------------------------
# Mail
# ---------------------------------------------------------------------------

# Only the first part of a message is fetched. An OTP is always near the top,
# and a marketing email can run to megabytes that we would otherwise pull over
# the wire and hold in memory for nothing.
FETCH_OCTETS = 16384

# Re-issue IDLE well inside the 30 minute limit RFC 2177 suggests servers
# enforce; Gmail drops idle connections at roughly that mark.
IDLE_REFRESH = 840


def idle_wait(imap, refresh):
    """Wait inside IDLE until the mailbox changes, or `refresh` elapses.

    The wait is ended by *sending DONE from a timer thread*, never by letting
    the read time out. An SSL socket read that times out poisons the
    connection - every read after it raises "cannot read from timed out
    object" - and an earlier version used exactly that as its refresh
    mechanism, tearing down and rebuilding all three connections every 14
    quiet minutes; any code arriving during a rebuild was skipped. With DONE
    doing the ending, the reader always sees a complete tagged line and the
    connection stays healthy across refreshes.

    The one socket timeout kept is a dead-link safety net at refresh+60s. If
    it ever fires, DONE went unanswered for a minute, the session is being
    torn down regardless, and the poisoned state costs nothing.

    Returns True when the server reported new mail. Raises RuntimeError when
    the server refuses IDLE (caller should fall back to polling), and lets
    connection errors propagate (caller should reconnect).
    """
    tag = imap._new_tag()
    imap.sock.settimeout(refresh + 60)
    imap.send(b"%s IDLE\r\n" % tag)
    if not imap.readline().startswith(b"+"):
        imap.tagged_commands.pop(tag, None)
        raise RuntimeError("server did not accept IDLE")

    once = threading.Lock()
    sent = [False]

    def send_done():
        # Exactly once: DONE is only valid while IDLE is open, and both the
        # timer and the activity path below can try to end it.
        with once:
            if sent[0]:
                return
            sent[0] = True
        try:
            imap.send(b"DONE\r\n")
        except Exception:
            pass

    timer = threading.Timer(refresh, send_done)
    timer.daemon = True
    timer.start()
    activity = False
    try:
        while True:
            line = imap.readline()
            if not line:
                raise ConnectionError("connection closed during IDLE")
            if line.startswith(tag):
                break                     # tagged completion - IDLE is over
            if b"EXISTS" in line or b"RECENT" in line:
                activity = True
                send_done()               # read on until the tagged line
    finally:
        timer.cancel()
        # The raw send/readline exchange bypassed imaplib's own machinery, so
        # its per-tag bookkeeping has to be cleaned up by hand.
        imap.tagged_commands.pop(tag, None)
        imap.sock.settimeout(60)          # ordinary command reads
    return activity


def _decode_header(raw):
    if not raw:
        return ""
    try:
        parts = email.header.decode_header(raw)
        out = []
        for text, enc in parts:
            if isinstance(text, bytes):
                out.append(text.decode(enc or "utf-8", errors="replace"))
            else:
                out.append(text)
        return "".join(out).strip()
    except Exception:
        return str(raw)


def _body_text(msg):
    """Best-effort plain text, preferring text/plain over text/html."""
    plain, html_part = "", ""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype not in ("text/plain", "text/html"):
                    continue
                try:
                    raw = part.get_payload(decode=True) or b""
                    text = raw.decode(part.get_content_charset() or "utf-8",
                                      errors="replace")
                except Exception:
                    continue
                if ctype == "text/plain" and not plain:
                    plain = text
                elif ctype == "text/html" and not html_part:
                    html_part = text
        else:
            raw = msg.get_payload(decode=True) or b""
            text = raw.decode(msg.get_content_charset() or "utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                html_part = text
            else:
                plain = text
    except Exception:
        pass
    return plain or html_part


class MailWatcher(threading.Thread):
    """Watches one IMAP mailbox for arriving OTPs.

    Uses IDLE where the server offers it, so a code appears within a second or
    two of landing. Any problem with IDLE - an old server, a proxy that mangles
    it, a Python change under the private imaplib calls this needs - degrades
    to polling rather than failing, because a slow OTP screen beats none.
    """

    def __init__(self, cfg, dispatcher):
        # One thread per mailbox, named after it: with several accounts running,
        # a stack trace or a log line has to say which one it came from.
        self.label = str(cfg.get("label") or cfg.get("user") or "mail")
        super().__init__(name="otp-mail-%s" % self.label, daemon=True)
        self.cfg = cfg
        self.dispatcher = dispatcher
        self._stop = threading.Event()
        self._use_idle = True
        # None until the first connection establishes the mailbox tip; kept
        # across reconnects so a dropped connection does not lose messages.
        self._last_uid = None
        self.poll_seconds = int(cfg.get("poll_seconds") or 15)
        self.max_age = int(cfg.get("max_age_seconds") or 180)

    def stop(self):
        self._stop.set()

    # -- message handling ---------------------------------------------------
    def _scan(self, imap, uids):
        for uid in uids:
            if self._stop.is_set():
                return
            try:
                typ, data = imap.uid("FETCH", uid,
                                     "(BODY.PEEK[]<0.%d>)" % FETCH_OCTETS)
                if typ != "OK" or not data or not isinstance(data[0], tuple):
                    continue
                msg = email.message_from_bytes(data[0][1])
            except Exception:
                log.debug("Could not fetch uid %s", uid, exc_info=True)
                continue

            # Old mail is not an OTP worth interrupting the panel for. This
            # matters on reconnect, when the server hands us a backlog.
            try:
                sent = email.utils.parsedate_to_datetime(msg.get("Date"))
                if sent and time.time() - sent.timestamp() > self.max_age:
                    continue
            except Exception:
                pass

            subject = _decode_header(msg.get("Subject"))
            sender = _decode_header(msg.get("From"))
            code, score, why = otp.find_code(subject, _body_text(msg), sender)
            if code:
                log.info("[%s] Mail OTP %s (score %d: %s)",
                         self.label, code, score, ", ".join(why))
                self.dispatcher.offer(code, otp.friendly_sender(sender), "email",
                                      self.cfg.get("label") or "")
            else:
                log.debug("[%s] No OTP in %r (%s)",
                          self.label, subject[:60], ", ".join(why))

    def _session(self):
        import imaplib

        host = self.cfg.get("host") or "imap.gmail.com"
        port = int(self.cfg.get("port") or 993)
        folder = self.cfg.get("folder") or "INBOX"

        imap = imaplib.IMAP4_SSL(host, port)
        try:
            imap.login(self.cfg["user"], self.cfg["app_password"])
            imap.select(folder)

            # On the FIRST connection everything already in the mailbox is
            # history, so start from the current tip and do not replay it.
            #
            # On a RECONNECT, resume from the last uid actually processed
            # instead. Re-reading the tip would silently swallow anything that
            # landed while the connection was down, which is exactly when a
            # code goes missing and the panel appears to have stopped working.
            # max_age_seconds still guards against acting on anything stale.
            if self._last_uid is None:
                typ, data = imap.uid("SEARCH", None, "ALL")
                self._last_uid = 0
                if typ == "OK" and data and data[0]:
                    self._last_uid = int(data[0].split()[-1])
                log.info("OTP mail watch [%s] on %s/%s from uid %d",
                         self.label, host, folder, self._last_uid)
            else:
                log.info("OTP mail watch [%s] resumed on %s/%s from uid %d",
                         self.label, host, folder, self._last_uid)
            last = self._last_uid

            supports_idle = b"IDLE" in (imap.capability()[1][0] or b"")
            if not supports_idle:
                log.info("Server does not advertise IDLE; polling every %ds",
                         self.poll_seconds)
                self._use_idle = False

            while not self._stop.is_set():
                if self._use_idle:
                    try:
                        idle_wait(imap, IDLE_REFRESH)
                    except RuntimeError as e:
                        # The server took the connection but refused IDLE.
                        # Poll for the rest of this session rather than
                        # reconnect-looping against a server that will refuse
                        # again. Connection errors are NOT caught here: those
                        # need a fresh connection, so they propagate to run().
                        log.warning("[%s] IDLE refused (%s); polling every %ds",
                                    self.label, e, self.poll_seconds)
                        self._use_idle = False
                else:
                    if self._stop.wait(self.poll_seconds):
                        break
                    imap.noop()

                typ, data = imap.uid("SEARCH", None, "UID %d:*" % (last + 1))
                if typ != "OK" or not data or not data[0]:
                    continue
                # "UID n:*" always returns at least one message even when
                # nothing is new, so the uid has to be re-checked here.
                uids = [u for u in data[0].split() if int(u) > last]
                if not uids:
                    continue
                last = max(int(u) for u in uids)
                self._last_uid = last     # survives a reconnect
                self._scan(imap, uids)
        finally:
            try:
                imap.logout()
            except Exception:
                pass

    def run(self):
        backoff = 5
        while not self._stop.is_set():
            try:
                self._session()
                backoff = 5
            except Exception as e:
                log.warning("[%s] mail watcher: %s; reconnecting in %ds",
                            self.label, e, backoff)
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 300)


# ---------------------------------------------------------------------------
# Windows toast notifications
# ---------------------------------------------------------------------------

class ToastWatcher(threading.Thread):
    """Reads Windows toasts and scores them for OTPs.

    Verified working from an unpackaged process on this machine: the listener
    returns Allowed and exposes each toast's package family name, so filtering
    by source app is exact rather than title matching.
    """

    def __init__(self, cfg, dispatcher):
        super().__init__(name="otp-toast", daemon=True)
        self.cfg = cfg
        self.dispatcher = dispatcher
        self._stop = threading.Event()
        self.ignore = [s.lower() for s in (cfg.get("ignore_apps") or [])]

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            asyncio.run(self._loop())
        except Exception:
            log.exception("OTP toast watcher stopped")

    async def _loop(self):
        try:
            from winrt.windows.ui.notifications.management import (
                UserNotificationListener, UserNotificationListenerAccessStatus)
            from winrt.windows.ui.notifications import NotificationKinds
        except Exception:
            log.warning("winrt notification packages missing; toast OTPs disabled")
            return

        listener = UserNotificationListener.current
        if await listener.request_access_async() != UserNotificationListenerAccessStatus.ALLOWED:
            log.warning("Notification access denied; toast OTPs disabled")
            return

        # Whatever is already in the Action Center is history, not an arrival.
        seen = {n.id for n in
                await listener.get_notifications_async(NotificationKinds.TOAST)}
        log.info("OTP toast watch active (%d existing toasts ignored)", len(seen))

        while not self._stop.is_set():
            try:
                for n in await listener.get_notifications_async(NotificationKinds.TOAST):
                    if n.id in seen:
                        continue
                    seen.add(n.id)
                    self._consider(n)
            except Exception:
                log.debug("Toast read failed", exc_info=True)
            await asyncio.sleep(1)

    def _consider(self, note):
        try:
            app = note.app_info.display_info.display_name or ""
            family = note.app_info.package_family_name or ""
        except Exception:
            app, family = "", ""
        if any(i in app.lower() or i in family.lower() for i in self.ignore):
            return
        try:
            texts = [el.text for el in
                     note.notification.visual.bindings[0].get_text_elements()]
        except Exception:
            return
        if not texts:
            return
        # A toast is title + body. Treating the title as a subject gives the
        # scorer the same shape it sees for mail.
        code, score, why = otp.find_code(texts[0], " ".join(texts[1:]), app)
        if code:
            log.info("Toast OTP %s from %s (score %d: %s)",
                     code, app, score, ", ".join(why))
            self.dispatcher.offer(code, app or "Notification", "notification")


# ---------------------------------------------------------------------------

def accounts(mail_cfg):
    """One resolved config per mailbox.

    Keys set directly under `email:` are defaults every account inherits, so a
    shared `host` or `max_age_seconds` is written once. A single mailbox can
    still be configured flat, with no `accounts:` list at all.
    """
    shared = {k: v for k, v in mail_cfg.items()
              if k not in ("accounts", "enabled")}
    listed = mail_cfg.get("accounts")
    if listed:
        for entry in listed:
            merged = dict(shared)
            merged.update(entry or {})
            yield merged
    elif mail_cfg.get("user"):
        yield dict(shared)


def start(cfg, on_otp):
    """Start every enabled source. Returns the list of running watchers."""
    dispatcher = Dispatcher(on_otp)
    started = []

    mail_cfg = (cfg.get("email") or {})
    if mail_cfg.get("enabled"):
        configured = list(accounts(mail_cfg))
        if not configured:
            log.warning("OTP email enabled but no account is configured in services.yaml")
        for account in configured:
            if not account.get("user") or not account.get("app_password"):
                log.warning("Skipping OTP mailbox %r: user or app_password missing",
                            account.get("label") or account.get("user") or "?")
                continue
            w = MailWatcher(account, dispatcher)
            w.start()
            started.append(w)

    toast_cfg = (cfg.get("notifications") or {})
    if toast_cfg.get("enabled"):
        w = ToastWatcher(toast_cfg, dispatcher)
        w.start()
        started.append(w)

    if not started:
        log.info("No OTP sources enabled")
    return started
