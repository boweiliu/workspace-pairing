# Workspace pairing: invite, gate, and peer client

Lets another workspace view an app of yours live, with no account sign-in, while
strangers are refused. The other workspace pairs once by redeeming a single-use invite
you DM it; after that, every request it makes is signed with its own key, and you can
revoke it at any time -- including cutting a live stream it already has open.

The app being shared here is the example `task-status` card one directory up. Nothing in
the pairing is specific to it: the gate forwards to whatever HTTP app listens on
`STATUS_GATE_UPSTREAM_PORT` (default 8095, the card's port), so pointing it at an app of
your own is a matter of setting that in `system/supervisord.conf.d/task-status-gate.conf`.

```
viewer's app --signed GET--> ngrok (STATUS_TUNNEL_DOMAIN) --> task-status-gate :8096 --> task-status :8095
```

Two supervisord programs:

- `task-status-gate` (`gate.py`, stdlib): the tunnel's only target. Lets a GET/HEAD through only
  when its `X-Status-Auth` header is signed by a key in
  `data/.apps/task-status/allowed_signers` (OpenSSH allowed-signers format); responses stream
  unbuffered so the card's live-update stream passes through, and a stream is cut mid-flight if
  its viewer's line is removed while it's still open (checked every 30s). The header and message
  format are documented in `gate.py`. It also serves `POST /__pairing/accept`, the invite-redeem
  endpoint below.
- `task-status-tunnel` (`run_tunnel.sh`): the ngrok agent, run under `with_secrets.py` with the
  tunnel key in `data/.secrets/status-tunnel.env`. The address is `STATUS_TUNNEL_DOMAIN` in
  `system/supervisord.conf.d/task-status-tunnel.conf`; the binary is fetched into
  `data/.state/status_tunnel/bin/` on first start.

## Pairing with a new viewer, by invite

This is the normal way to add a viewer now -- no hand-editing `allowed_signers`.

1. On this side: `python3 system/apps/task_status/tunnel/create_invite.py <principal>`. Prints
   a blob (address, principal, single-use code, 24h expiry). DM it to the peer over a private
   channel. By design, the code's single use and the DM's privacy are the only
   anti-interception measures; there is no separate fingerprint step.
2. On the peer's side: `python3 peer_client/pair.py <address> <principal> <code>` (see
   `peer_client/README.md` -- it's meant to run standalone in the peer's own workspace).
   Generates their key, posts it with the code to `/__pairing/accept`, and on success the gate
   appends their line to `allowed_signers` itself.
3. To view: `python3 peer_client/view.py <principal>` fetches the card once, signing the
   request. It's a v0 stand-in for a real viewer app, one that re-signs and re-fetches on its
   own. (Built-in sharing needs the viewer to sign in with a browser, which is the gap this
   works around.)

## Adding or removing a viewer by hand

Still possible, e.g. for a one-off you don't want to invite through the flow above. Add one
line per viewer:

```
<principal> namespaces="status-card" ssh-ed25519 AAAA... <comment>
```

Deleting the line refuses that viewer's next request, and (since the revocation recheck was
added) cuts an already-open stream within 30 seconds too.

## Rotating the tunnel key

```bash
latchkey curl -s -X POST https://api.ngrok.com/credentials -H 'Ngrok-Version: 2' \
    -H 'Content-Type: application/json' -d '{"description": "status card tunnel"}' \
  | python3 system/apps/task_status/tunnel/store_tunnel_key.py
supervisorctl restart task-status-tunnel
```

then delete the old credential in the ngrok dashboard. On ngrok's free plan a key cannot be
restricted to one address (tunnel ACLs need Pay-as-you-go).
