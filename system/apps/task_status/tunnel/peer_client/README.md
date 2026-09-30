# status-card peer client

Run this in the workspace being invited to view someone else's status card -- not in
the workspace sharing it. Two scripts, standard library plus the `ssh-keygen` binary,
no dependency on the rest of this repo, so this directory is what would ship in a
template for the peer to install.

1. `pair.py <address> <principal> <code>` -- redeem the invite the owner sent you by
   DM. Makes your key (once, reused for any other peer) and registers it.
2. `view.py <principal>` -- fetch the card once, signed with your key.

State (your key, and which peers you've paired with) lives under
`data/.apps/status-card-viewer/` by default (override with `PEER_CLIENT_STATE_DIR`).

`view.py` fetches once; it isn't a live view. A real viewer app -- one that re-fetches
and re-signs on its own and renders the card properly -- is a follow-on, not built yet.
