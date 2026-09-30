#!/usr/bin/env python3
"""Show a peer's shared status card, signing the request with your paired key.

    python3 view.py <principal>

Fetches the card once, over the address saved by ``pair.py``, and prints the raw HTML
to stdout. This is a v0 stand-in, not a live view: a browser bookmark of the plain URL
would not work, because every request needs a fresh signature. A real viewer -- one
that re-signs and re-fetches on its own -- is the follow-on piece, not built yet.

Standard library plus the ssh-keygen binary.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

STATE_DIR = Path(os.environ.get("PEER_CLIENT_STATE_DIR", "data/.apps/status-card-viewer"))
KEY_PATH = STATE_DIR / "peer_key"
PEERS_FILE = STATE_DIR / "peers.json"
NAMESPACE = "status-card"
TOKEN_LIFETIME_SECONDS = 60


def _sign(principal: str, expires: int) -> str:
    message = f"{NAMESPACE}\n{principal}\n{expires}\n".encode()
    with tempfile.TemporaryDirectory() as scratch:
        message_path = Path(scratch) / "message"
        message_path.write_bytes(message)
        subprocess.run(
            ["ssh-keygen", "-Y", "sign", "-n", NAMESPACE, "-f", str(KEY_PATH), str(message_path)],
            check=True,
            capture_output=True,
        )
        armored = message_path.with_suffix(".sig").read_text()
    return "".join(armored.splitlines()[1:-1])


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <principal>", file=sys.stderr)
        return 2
    principal = sys.argv[1]
    peers = json.loads(PEERS_FILE.read_text()) if PEERS_FILE.exists() else {}
    peer = peers.get(principal)
    if peer is None:
        print(f"no paired peer named {principal!r}; run pair.py first", file=sys.stderr)
        return 1
    expires = int(time.time()) + TOKEN_LIFETIME_SECONDS
    signature = _sign(principal, expires)
    request = urllib.request.Request(peer["address"], headers={"X-Status-Auth": f"{principal} {expires} {signature}"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            sys.stdout.write(response.read().decode())
    except urllib.error.HTTPError as error:
        print(f"can't view it: {error.code} {error.reason} -- {error.read().decode().strip()}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
