#!/usr/bin/env python3
"""Store a freshly minted ngrok tunnel key for the task-status-tunnel service.

Reads the JSON the ngrok API returns from ``POST /credentials`` on stdin and writes
its token to the service's secret env file (mode 600), so the value never appears
in a tool call, a log, or a transcript. Prints only the credential id.

Usage, from the repo root::

    latchkey curl -s -X POST https://api.ngrok.com/credentials \\
        -H 'Ngrok-Version: 2' -H 'Content-Type: application/json' \\
        -d '{"description": "...", "acl": ["bind:<domain>"]}' \\
      | python3 system/apps/task_status/tunnel/store_tunnel_key.py

Standard library only.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ENV_FILE = Path("data") / ".secrets" / "status-tunnel.env"


def main() -> int:
    response = json.load(sys.stdin)
    token = response.get("token")
    if not token:
        # An error response carries no token, so its message is safe to show.
        print(f"error: {response.get('error_code')} {response.get('msg')}", file=sys.stderr)
        return 1
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = ENV_FILE.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(f"NGROK_AUTHTOKEN='{token}'\n")
    os.replace(temporary, ENV_FILE)
    print(f"stored tunnel key {response.get('id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
