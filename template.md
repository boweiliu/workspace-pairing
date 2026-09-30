---
title: "Workspace pairing: share a live app between two Minds"
description: "Pair two Mind workspaces with a single-use, expiring invite so one can view something the other is sharing live, with no account sign-in. Demonstrated with a simple example status card app."
thumbnail: "template.svg"
version: v1
format: v2
---

# Workspace pairing: share a live app between two Minds

This file is the manifest for the **Workspace pairing: share a live app between two Minds** template (slug:
`workspace-pairing`). It is the one document a future agent reads to understand,
present, and adapt this template. If you are an agent in a mind that was
created from this template, this file is your script: read all of it, then
follow "How to adapt it" below.

## What it is

Pair two Mind workspaces with a single-use, expiring invite so one can view something the other is sharing live, with no account sign-in. Demonstrated with a simple example status card app.

Lets one Mind workspace view something another workspace is sharing -- live, with
no account sign-in on either side -- while everyone else is refused. The sharing
workspace mints an invite that works once and expires in 24 hours, and sends it to
the other workspace's owner by DM. The other workspace redeems it once, which
registers a key it generated for itself; from then on, every request it makes is
signed with that key and checked before anything is let through. The owner can
revoke a viewer at any time by deleting one line, which refuses the viewer's next
request and also cuts a live stream they already have open, within 30 seconds.

That pairing protocol -- invite, redeem, signed requests, revoke -- is the feature.
The thing being shared in this template is an **example app**: a small status card
(a title, a one-line status, a few links, and an active/idle dot) that updates live
whenever its status file changes. It is included so the whole exchange works end to
end out of the box, over a real public tunnel; swap in your own app to share
something else.

## How it works

The snapshot includes these paths (each is a repo-root-relative path copied
from the original mind onto a clean default-workspace-template base):

- `system/apps/task_status`
- `system/supervisord.conf.d/task-status.conf`
- `system/supervisord.conf.d/task-status-gate.conf`
- `system/supervisord.conf.d/task-status-tunnel.conf`

**The pairing mechanism** (`system/apps/task_status/tunnel/`, standard library plus
the stock `ssh-keygen` binary):

- `gate.py` -- the signature gate, run by `task-status-gate.conf` on `127.0.0.1:8096`.
  It is the tunnel's only target, so everything reaching it came from outside. It
  forwards a GET/HEAD to the shared app only when the request's `X-Status-Auth`
  header (`<principal> <expires> <signature>`) carries a signature that
  `ssh-keygen -Y verify` accepts against `data/.apps/task-status/allowed_signers`
  (OpenSSH allowed-signers format, namespace `status-card`, tokens valid for at most
  an hour); anything else gets a 401. Responses stream through unbuffered, so the
  app's live-update stream reaches the viewer as it happens, and while a stream is
  open the gate rechecks every 30 seconds that the viewer is still listed, cutting
  the connection the moment they are not. It also handles one path itself,
  `POST /__pairing/accept`, where a peer redeems an invite.
- `invites.py` and `create_invite.py` -- mint an invite: a random 192-bit code bound
  to the principal name the owner chose for the peer, single-use and expiring in 24
  hours, stored in `data/.apps/task-status/invites.json`. `create_invite.py` prints
  the address, principal, code, and expiry as a block to paste into a DM.
- `peer_client/` -- the half the *viewing* workspace runs; it depends on nothing
  else in this repo, so it can be copied on its own. `pair.py` generates the peer's
  own ed25519 key (once, under `data/.apps/status-card-viewer/`), posts its public
  half with the invite code to `/__pairing/accept`, and remembers the address.
  `view.py` signs a 60-second token with that key and fetches the shared page once.
- `run_tunnel.sh` -- run by `task-status-tunnel.conf` under `with_secrets.py`, so the
  ngrok auth token reaches only this process. It downloads the ngrok agent into
  `data/.state/status_tunnel/bin/` on first start, then publishes the gate at
  `https://$STATUS_TUNNEL_DOMAIN`. `store_tunnel_key.py` is a helper that writes a
  freshly minted ngrok credential into the secret file without the value passing
  through a terminal or a log.

**The protocol, end to end:** the owner runs `create_invite.py <principal>` and DMs
the result. The peer runs `pair.py <address> <principal> <code>`; the gate spends the
code (it must be known, unused, and unexpired), confirms it was issued for that
principal, and appends the peer's key to `allowed_signers` itself. Each later request is signed by
the peer and verified by the gate. Deleting the peer's line from `allowed_signers`
revokes them: the next request is refused, and an already-open stream is cut within
30 seconds. There is deliberately no key-fingerprint comparison step: a code that
works once, expires within a day, and travels over a private DM is the protection
against interception.

**The example payload** (`system/apps/task_status/`, a Flask app): run by
`task-status.conf`, which registers it with `forward_port.py` from its `app.toml`
and serves it on port 8095, so it opens as a normal window on the owner's own
desktop. It renders `data/.apps/task-status/status.json` as a card at `/`, and
`/events` is a server-sent-events stream that pushes the card again whenever that
file changes (checked twice a second, with a keep-alive every 15 seconds so tunnels
hold the connection open). The gate forwards to whatever listens on
`STATUS_GATE_UPSTREAM_PORT` (default 8095), so sharing a different app is a matter of
pointing that at it.

```
viewer --signed GET--> ngrok (STATUS_TUNNEL_DOMAIN) --> task-status-gate :8096 --> task-status :8095
```

## Recipe

This template is version `v1`. It is not a fork of the
workspace it came from -- it is DERIVED from it by a recipe: include these
paths, leave these out, apply these published-version rules. An update re-runs
the recipe against the current workspace and publishes the result as the next
version, so anything excluded stays excluded even though it still exists in the
source workspace.

The recipe is machine-read, so it lives in the sibling
[`template.toml`](template.toml) -- its `[recipe]` table -- along with
the structured requirements and the environment this template needs
installed. That file is authoritative for all of it; this one holds the prose.

## Requirements

Everything the adopting mind must deal with before this template is really
theirs. Two kinds of entry, handled at different times:

- **Activation** -- what must be SET UP before anything runs, in the
  machine-readable `requires_` forms below. The adopting agent acts on these
  ITSELF, first, before asking anything.
- **Adaptation** -- what must be DECIDED or REWIRED, in prose. Worked through
  interactively with the user, after activation.

Activation:

- requires_secret: data/.secrets/status-tunnel.env with NGROK_AUTHTOKEN (an ngrok auth token, for the tunnel that publishes the gate at a fixed public address)

No latchkey permission is needed: nothing in the included code calls an external
API through latchkey. The only outside services it touches are the ngrok tunnel
(authenticated by the secret above) and a one-time download of the ngrok agent from
ngrok's public download address. The tunnel README's optional key-rotation recipe
calls the ngrok API through `latchkey curl`, but that is a manual convenience; the
same key can be created in the ngrok dashboard instead.

No LLM access is needed: nothing in this template calls Claude or any other model.

Adaptation:

- **Your own ngrok account and tunnel domain.** The published copy uses the
  placeholder domain `your-tunnel-domain.ngrok-free.dev` in two places: the
  `environment=` line of `system/supervisord.conf.d/task-status-tunnel.conf`, and
  the fallback default in `system/apps/task_status/tunnel/create_invite.py`. The
  adopter needs their own ngrok account (the free plan gives one fixed address per
  account), their own auth token for the secret above (the dashboard's authtoken,
  or a dedicated credential minted per the tunnel README), and their own domain set
  in both places, then `supervisorctl restart task-status-tunnel`.
- **The example card starts empty.** Its content,
  `data/.apps/task-status/status.json`, is not shipped, so the card reads "No task
  loaded" until the adopter writes one (`title`, `status`, `links` as a
  label-to-URL map, `agent_active`) -- or points the gate at an app of their own
  instead.
- **The viewing workspace needs the peer client.** A workspace that wants to view a
  share needs `system/apps/task_status/tunnel/peer_client/` (copied on its own, or
  by adopting this template too). No viewers are paired at first:
  `allowed_signers` and the invite list start empty, so nothing is reachable
  through the tunnel until an invite is minted and redeemed.

## Environment

What this template needs INSTALLED, beyond what the template already has.
Declared in `template.toml`'s `[environment]` table; an adopting mind
converges it at ITS OWN pinned apt snapshot timestamp, so package versions come
out consistent with the rest of that mind's environment rather than frozen to
whatever this publisher happened to have.

Nothing extra -- runs on the stock workspace environment.

The gate and peer client shell out to `ssh-keygen`, and the tunnel script uses
`curl` and `tar`; all three ship in the stock image. The ngrok agent is not an
installed dependency: `run_tunnel.sh` downloads it into
`data/.state/status_tunnel/bin/` the first time the tunnel starts.

## How to adapt it

Instructions for the NEXT agent -- the one adapting this template into a
new mind. This is the `use-template` skill's template path; in short:

1. Read this entire file first, especially "Requirements" below. It holds two
   kinds of entry and they are handled at different times: the machine-readable
   `requires_` lines are ACTIVATION (set them up before anything runs), and
   the prose bullets are ADAPTATION (decide or rewire them afterwards).
2. Present the template to the user in plain, non-technical language: what
   it is, what it does, and what it needs from them (name the activation
   requirements).
3. Ask whether they want to use the same connectors (e.g. their own Slack).
   If YES: ACTIVATE FIRST -- initiate every `requires_permission` line NOW
   via a latchkey permission request (see the `latchkey` skill; the request
   opens the approval/login flow in the minds app), wire up any
   `requires_secret` values, start the services, and get the app showing
   THE USER'S OWN DATA. Done for a data-backed app means the user can open it
   and see their own data -- NOT that a service starts or an endpoint returns
   200. Then tell them it is live and to take a look.
4. Only AFTER that (or immediately, if they chose different connectors -- the
   swap is then the first adaptation) ask: "How do you want to adapt it?"
5. Work through each requirement interactively, one at a time. Translate each
   into plain language, ask for a decision only when you genuinely need one,
   and resolve the obvious ones yourself.
6. When done, append a dated entry to "Adaptation history" below (never
   rewrite earlier entries) and commit.

## Publication history

This template's changelog: what each published version changed. The PUBLISHER
appends one entry per version (newest last); earlier entries are never rewritten.
This is distinct from "Adaptation history" below, which is the ADOPTERS' log.

### v1 (2026-09-30) -- first publish: the invite-based pairing protocol (gate, invite exchange, peer client), demonstrated with an example live status-card app.

## Adaptation history

Each mind that adapts this template appends one dated entry below. Earlier
entries are never rewritten.
