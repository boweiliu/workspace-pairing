# task-status

Example shared app: a status card (a title, a one-line status, a few links, and an
active/idle dot) that updates live as `data/.apps/task-status/status.json` changes.

It is included as a working example of something shared live through the pairing
mechanism in `tunnel/` -- the invite, the signature gate, and the peer client, which are
the actual feature. To share something else, swap in your own app and point the gate at
it instead; see `tunnel/README.md`.
