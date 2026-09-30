#!/usr/bin/env python3
"""Redeem a pairing invite for a peer's status card.

This is the piece a *peer* workspace runs -- the one being invited, not the one
sharing. Run it once with the invite the owner sent you by DM::

    python3 pair.py <address> <principal> <code>

e.g. ``python3 pair.py https://your-tunnel-domain.ngrok-free.dev
peer-studio cePGAu16...``. This generates your own ed25519 key (once -- it's reused for
any later invite from any other peer) at ``KEY_PATH``, sends its public half to the
owner's gate together with the invite code, and remembers the owner's address under
``PEERS_FILE`` so ``view.py`` knows where to look. Nothing about you reaches the owner
except this key and whichever principal name they chose for you in the invite.

Standard library only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

STATE_DIR = Path(os.environ.get("PEER_CLIENT_STATE_DIR", "data/.apps/status-card-viewer"))
KEY_PATH = STATE_DIR / "peer_key"
PEERS_FILE = STATE_DIR / "peers.json"


def _ensure_key() -> None:
    if KEY_PATH.exists():
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(KEY_PATH), "-C", "status-card-viewer"],
        check=True,
        capture_output=True,
    )


def _public_key() -> str:
    key_type, key_data = KEY_PATH.with_suffix(".pub").read_text().split()[:2]
    return f"{key_type} {key_data}"


def _save_peer(principal: str, address: str) -> None:
    peers = json.loads(PEERS_FILE.read_text()) if PEERS_FILE.exists() else {}
    peers[principal] = {"address": address.rstrip("/")}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PEERS_FILE.write_text(json.dumps(peers, indent=2, sort_keys=True) + "\n")


def main() -> int:
    if len(sys.argv) != 4:
        print(f"usage: {sys.argv[0]} <address> <principal> <code>", file=sys.stderr)
        return 2
    address, principal, code = sys.argv[1], sys.argv[2], sys.argv[3]
    _ensure_key()
    body = json.dumps({"code": code, "principal": principal, "public_key": _public_key()}).encode()
    request = urllib.request.Request(
        f"{address.rstrip('/')}/__pairing/accept",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as error:
        result = json.loads(error.read())
    if not result.get("ok"):
        print(f"pairing failed: {result.get('error')}", file=sys.stderr)
        return 1
    _save_peer(principal, address)
    print(f"paired as {principal!r} with {address}. Run: python3 view.py {principal}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
