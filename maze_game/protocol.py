from __future__ import annotations
import json
import socket
from typing import Any

MAX_MESSAGE = 1_000_000


def send_message(sock: socket.socket, payload: dict[str, Any], lock=None) -> None:
    data = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
    if len(data) > MAX_MESSAGE:
        raise ValueError("message too large")
    if lock:
        with lock:
            sock.sendall(data)
    else:
        sock.sendall(data)


def receive_messages(sock: socket.socket, buffer: bytearray):
    chunk = sock.recv(65536)
    if not chunk:
        raise ConnectionError("peer disconnected")
    buffer.extend(chunk)
    if len(buffer) > MAX_MESSAGE:
        raise ValueError("receive buffer too large")
    while True:
        newline = buffer.find(b"\n")
        if newline < 0:
            break
        raw = bytes(buffer[:newline])
        del buffer[:newline + 1]
        if raw:
            value = json.loads(raw.decode("utf-8"))
            if isinstance(value, dict):
                yield value
