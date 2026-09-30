#!/usr/bin/env python3
"""Mint a pairing invite for the task-status card, to send to a peer workspace by DM.

Usage, from the repo root::

    python3 system/apps/task_status/tunnel/create_invite.py <principal>

The printed address is ``https://$STATUS_TUNNEL_DOMAIN``, falling back to the
placeholder default below. Use your own tunnel domain (the same value as in
``system/supervisord.conf.d/task-status-tunnel.conf``), either by changing that
default or per run::

    STATUS_TUNNEL_DOMAIN=your-tunnel-domain.ngrok-free.dev python3 system/apps/task_status/tunnel/create_invite.py <principal>

``<principal>`` is a short name for the peer (e.g. ``peer-studio``) -- it becomes the
identity attached to their requests once they pair. Prints the exact blob to paste into
a private DM; the invite is single-use and expires in 24 hours, so there is no
fingerprint step on either side: by design, the code working only once and the DM
being private are the protection against interception.

Standard library only.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import invites  # noqa: E402

STATUS_TUNNEL_DOMAIN = os.environ.get("STATUS_TUNNEL_DOMAIN", "your-tunnel-domain.ngrok-free.dev")


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <principal>", file=sys.stderr)
        return 2
    principal = sys.argv[1]
    record = invites.create(principal)
    expires = datetime.fromtimestamp(record["expires"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print("Paste this to the peer over a private DM (works once, expires in 24h):")
    print()
    print(f"  address: https://{STATUS_TUNNEL_DOMAIN}")
    print(f"  principal: {principal}")
    print(f"  code: {record['code']}")
    print(f"  expires: {expires}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
