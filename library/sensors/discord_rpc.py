#!/usr/bin/env python
"""Reads voice channel state from the local Discord client over its IPC socket.

Discord exposes a named pipe (\\\\.\\pipe\\discord-ipc-N) that speaks a small
JSON protocol. Reading voice state needs the `rpc.voice.read` scope, which
Discord normally gates behind app approval - but the application's own owner and
its testers are exempt, so a personal app works on the owner's account.

Auth is a one-time dance:
    HANDSHAKE  -> AUTHORIZE (user clicks Approve in Discord)
               -> exchange the returned code for a token (needs client secret)
               -> AUTHENTICATE
The token is cached on disk, so the popup only appears once.

Everything runs on a daemon thread with class-level state: the theme loop builds
a fresh sensor object every tick, and must never block on IO.
"""
import json
import os
import struct
import threading
import time
import uuid

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
SERVICES_FILE = os.path.join(APP_DIR, "services.yaml")
TOKEN_FILE = os.path.join(APP_DIR, "discord_token.json")

OP_HANDSHAKE = 0
OP_FRAME = 1
OP_CLOSE = 2

SCOPES = ["rpc", "rpc.voice.read"]


def _cfg():
    try:
        import yaml
        with open(SERVICES_FILE, encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("discord") or {}
    except Exception:
        return {}


class _Pipe:
    """Minimal framed-JSON transport over the Discord IPC named pipe."""

    def __init__(self):
        self.f = None

    def connect(self):
        last = None
        for i in range(10):
            path = r"\\.\pipe\discord-ipc-" + str(i)
            try:
                self.f = open(path, "r+b", buffering=0)
                return
            except Exception as e:
                last = e
        raise ConnectionError("no Discord IPC pipe available: " + str(last))

    def send(self, op, obj):
        payload = json.dumps(obj).encode("utf-8")
        self.f.write(struct.pack("<II", op, len(payload)) + payload)
        self.f.flush()

    def recv(self):
        head = self.f.read(8)
        if len(head) < 8:
            raise ConnectionError("pipe closed")
        op, ln = struct.unpack("<II", head)
        body = self.f.read(ln) if ln else b""
        return op, json.loads(body.decode("utf-8")) if body else {}

    def close(self):
        try:
            if self.f:
                self.f.close()
        except Exception:
            pass
        self.f = None


class Discord:
    """Cached voice state, refreshed by a background connection to Discord."""

    _started = False
    _lock = threading.Lock()

    connected = False
    status = "starting"          # short human-readable state for the panel
    channel = ""                 # voice channel name
    guild = ""                   # server name
    members = []                 # [{"name": str, "speaking": bool, "muted": bool}]
    self_muted = False
    self_deafened = False
    self_id = ""            # our own user id, learned at handshake
    recent = []             # [{"name", "action": joined|left, "ts"}] newest last
    _last_channel_id = None

    # -------------------------------------------------------------- auth
    @staticmethod
    def _load_token():
        try:
            with open(TOKEN_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("expires_at", 0) > time.time() + 60:
                return data.get("access_token")
        except Exception:
            pass
        return None

    @staticmethod
    def _save_token(token, expires_in):
        try:
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump({"access_token": token,
                           "expires_at": time.time() + int(expires_in or 0)}, f)
        except Exception:
            pass

    @classmethod
    def _authorize(cls, pipe, client_id, client_secret):
        """Ask Discord for a code, then trade it for a token. Shows a popup once."""
        nonce = str(uuid.uuid4())
        pipe.send(OP_FRAME, {"cmd": "AUTHORIZE", "nonce": nonce,
                             "args": {"client_id": client_id, "scopes": SCOPES}})
        cls.status = "waiting for approval in Discord"
        code = None
        # The user may take a while to click Approve; other events can arrive first.
        deadline = time.time() + 180
        while time.time() < deadline:
            op, msg = pipe.recv()
            if msg.get("nonce") == nonce:
                if msg.get("evt") == "ERROR":
                    raise PermissionError(msg.get("data", {}).get("message", "authorize failed"))
                code = msg.get("data", {}).get("code")
                break
        if not code:
            raise TimeoutError("no authorization code returned")

        import requests
        r = requests.post("https://discord.com/api/oauth2/token", timeout=20,
                          data={"client_id": client_id, "client_secret": client_secret,
                                "grant_type": "authorization_code", "code": code,
                                "redirect_uri": "http://localhost"})
        r.raise_for_status()
        tok = r.json()
        cls._save_token(tok.get("access_token"), tok.get("expires_in"))
        return tok.get("access_token")

    # ------------------------------------------------------------- helpers
    @classmethod
    def _request(cls, pipe, cmd, args=None, timeout=10):
        nonce = str(uuid.uuid4())
        pipe.send(OP_FRAME, {"cmd": cmd, "nonce": nonce, "args": args or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            op, msg = pipe.recv()
            if msg.get("nonce") == nonce:
                return msg
            cls._handle_event(msg)
        raise TimeoutError(cmd + " timed out")

    @classmethod
    def _subscribe(cls, pipe, evt, args=None):
        # Fire and forget: the SUBSCRIBE ack is not worth blocking the pump for.
        nonce = str(uuid.uuid4())
        pipe.send(OP_FRAME, {"cmd": "SUBSCRIBE", "evt": evt,
                             "nonce": nonce, "args": args or {}})

    @classmethod
    def _set_channel(cls, pipe, channel_id):
        if not channel_id:
            cls.channel = ""
            cls.guild = ""
            cls.members = []
            cls.status = "not in a voice channel"
            return
        msg = cls._request(pipe, "GET_CHANNEL", {"channel_id": channel_id})
        data = msg.get("data") or {}
        cls.channel = data.get("name") or "voice"
        gid = data.get("guild_id")
        if gid:
            try:
                g = cls._request(pipe, "GET_GUILD", {"guild_id": gid}, timeout=8)
                cls.guild = (g.get("data") or {}).get("name") or ""
            except Exception:
                cls.guild = ""          # DM / group call, or lookup refused
        else:
            cls.guild = ""
        previous = {m.get("id"): m for m in cls.members}
        speaking_before = {m.get("id") for m in cls.members if m.get("speaking")}

        cls.members = [{
            "name": (v.get("nick") or (v.get("user") or {}).get("global_name")
                     or (v.get("user") or {}).get("username") or "?"),
            # GET_CHANNEL does not report speaking, so carry it across refreshes
            # or a roster update would blank whoever is mid-sentence.
            "speaking": ((v.get("user") or {}).get("id")) in speaking_before,
            "muted": bool((v.get("voice_state") or {}).get("mute")
                          or (v.get("voice_state") or {}).get("self_mute")),
            "id": (v.get("user") or {}).get("id"),
            "joined_at": (previous.get((v.get("user") or {}).get("id")) or {}).get(
                "joined_at") or time.time(),
        } for v in (data.get("voice_states") or [])]

        # Diff against the previous roster to spot arrivals and departures. Only
        # meaningful within one channel: switching channels replaces everyone.
        if cls._last_channel_id == channel_id and previous:
            now_ids = {m.get("id") for m in cls.members}
            for m in cls.members:
                if m.get("id") not in previous:
                    cls.recent.append({"name": m["name"], "action": "joined", "ts": time.time()})
            for pid, pm in previous.items():
                if pid not in now_ids:
                    cls.recent.append({"name": pm["name"], "action": "left", "ts": time.time()})
        cls._last_channel_id = channel_id
        cls.recent = [r for r in cls.recent if time.time() - r["ts"] < 300][-12:]
        for v in (data.get("voice_states") or []):
            if ((v.get("user") or {}).get("id")) == cls.self_id:
                vs = v.get("voice_state") or {}
                cls.self_muted = bool(vs.get("self_mute") or vs.get("mute"))
                cls.self_deafened = bool(vs.get("self_deaf") or vs.get("deaf"))
        cls.status = "in voice"
        for evt in ("VOICE_STATE_CREATE", "VOICE_STATE_UPDATE", "VOICE_STATE_DELETE",
                    "SPEAKING_START", "SPEAKING_STOP"):
            cls._subscribe(pipe, evt, {"channel_id": channel_id})

    @classmethod
    def _handle_event(cls, msg):
        evt = msg.get("evt")
        data = msg.get("data") or {}
        if evt in ("SPEAKING_START", "SPEAKING_STOP"):
            uid = data.get("user_id")
            for m in cls.members:
                if m.get("id") == uid:
                    m["speaking"] = (evt == "SPEAKING_START")
        elif evt in ("VOICE_STATE_CREATE", "VOICE_STATE_DELETE", "VOICE_STATE_UPDATE"):
            cls._dirty = True

    # ---------------------------------------------------------------- loop
    @classmethod
    def ensure_started(cls):
        with cls._lock:
            if cls._started:
                return
            cls._started = True
            threading.Thread(target=cls._loop, name="discord-rpc", daemon=True).start()

    @classmethod
    def _loop(cls):
        while True:
            cfg = _cfg()
            client_id = str(cfg.get("client_id") or "").strip()
            client_secret = str(cfg.get("client_secret") or "").strip()
            if not client_id:
                cls.status = "no client_id set"
                cls.connected = False
                time.sleep(30)
                continue

            pipe = _Pipe()
            try:
                pipe.connect()
                pipe.send(OP_HANDSHAKE, {"v": 1, "client_id": client_id})
                op, msg = pipe.recv()
                if msg.get("evt") != "READY":
                    raise ConnectionError(str(msg)[:120])
                cls.self_id = ((msg.get("data") or {}).get("user") or {}).get("id") or ""

                token = cls._load_token()
                if not token:
                    if not client_secret:
                        cls.status = "no client_secret set"
                        cls.connected = False
                        pipe.close()
                        time.sleep(30)
                        continue
                    token = cls._authorize(pipe, client_id, client_secret)
                cls._request(pipe, "AUTHENTICATE", {"access_token": token})

                cls.connected = True
                cls._subscribe(pipe, "VOICE_CHANNEL_SELECT")
                sel = cls._request(pipe, "GET_SELECTED_VOICE_CHANNEL")
                data = sel.get("data")
                cls._set_channel(pipe, (data or {}).get("id") if data else None)

                # Event pump. Channel changes re-subscribe to the new channel.
                while True:
                    op, msg = pipe.recv()
                    if msg.get("evt") == "VOICE_CHANNEL_SELECT":
                        cls._set_channel(pipe, (msg.get("data") or {}).get("channel_id"))
                    else:
                        cls._handle_event(msg)
                        if getattr(cls, "_dirty", False):
                            cls._dirty = False
                            try:
                                sel = cls._request(pipe, "GET_SELECTED_VOICE_CHANNEL")
                                d = sel.get("data")
                                cls._set_channel(pipe, (d or {}).get("id") if d else None)
                            except Exception:
                                pass
            except PermissionError as e:
                # Scope refused: approval is genuinely required, retrying will not help.
                cls.status = "not authorised: " + str(e)[:40]
                cls.connected = False
                pipe.close()
                time.sleep(300)
            except Exception as e:
                cls.status = type(e).__name__
                cls.connected = False
                pipe.close()
                time.sleep(10)
