#!/usr/bin/env python
"""Probes the local Discord IPC pipe to see what a handshake actually returns."""
import json
import struct

PIPE = r"\\.\pipe\discord-ipc-0"

OP_HANDSHAKE = 0
OP_FRAME = 1
OP_CLOSE = 2


def send(f, op, obj):
    payload = json.dumps(obj).encode("utf-8")
    f.write(struct.pack("<II", op, len(payload)) + payload)
    f.flush()


def recv(f):
    head = f.read(8)
    if len(head) < 8:
        return None, None
    op, ln = struct.unpack("<II", head)
    body = f.read(ln) if ln else b""
    try:
        return op, json.loads(body.decode("utf-8"))
    except Exception:
        return op, body


def main():
    try:
        f = open(PIPE, "r+b", buffering=0)
    except Exception as e:
        print("could not open pipe:", type(e).__name__, e)
        return
    print("pipe opened:", PIPE)

    # Handshake with the real application ID. A READY reply confirms the ID is
    # valid and tells us which account Discord is logged in as.
    cid = client_id()
    if not cid:
        print("set discord.client_id in services.yaml first")
        f.close()
        return
    send(f, OP_HANDSHAKE, {"v": 1, "client_id": cid})
    op, data = recv(f)
    print("handshake reply  op:", op)
    print("payload:", json.dumps(data, indent=2)[:600] if isinstance(data, (dict, list)) else data)
    f.close()


if __name__ == "__main__":
    main()
