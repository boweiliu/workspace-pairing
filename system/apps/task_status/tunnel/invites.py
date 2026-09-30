"""Invite storage for the task-status pairing exchange.

An invite is a single-use, expiring code that lets a peer workspace register its own
public key as an allowed viewer over ``POST /__pairing/accept`` on the gate, instead of
the owner hand-editing ``allowed_signers``. See ``README.md`` for the full exchange.

Standard library only.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import time
from pathlib import Path

INVITES_FILE = Path(os.environ.get("STATUS_GATE_INVITES", "data/.apps/task-status/invites.json"))
INVITE_LIFETIME_SECONDS = 24 * 60 * 60
CODE_BYTES = 24  # secrets.token_urlsafe pads this out to ~32 characters, 192 bits.


def _load() -> dict:
    try:
        return json.loads(INVITES_FILE.read_text())
    except FileNotFoundError:
        return {}


def _save(invites: dict) -> None:
    INVITES_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = INVITES_FILE.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(invites, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, INVITES_FILE)


def _with_lock(body):
    INVITES_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_path = INVITES_FILE.with_suffix(".lock")
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            return body()
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def create(principal: str) -> dict:
    """Mint a new invite for ``principal`` and persist it. Returns the record plus its code."""

    def body() -> dict:
        code = secrets.token_urlsafe(CODE_BYTES)
        now = int(time.time())
        record = {"principal": principal, "created": now, "expires": now + INVITE_LIFETIME_SECONDS, "used": False}
        invites = _load()
        invites[code] = record
        _save(invites)
        return {"code": code, **record}

    return _with_lock(body)


def redeem(code: str) -> tuple[dict | None, str]:
    """Atomically mark an invite used. Returns (record, "") or (None, reason) if it can't be redeemed."""

    def body() -> tuple[dict | None, str]:
        invites = _load()
        record = invites.get(code)
        if record is None:
            return None, "unknown code"
        if record["used"]:
            return None, "code already used"
        if int(time.time()) >= record["expires"]:
            return None, "code expired"
        record["used"] = True
        invites[code] = record
        _save(invites)
        return record, ""

    return _with_lock(body)
