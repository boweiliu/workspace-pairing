"""Task status dashboard for agent work.

Services run from /home/user/workspace (the repo root). Conventions:

- Persistent state (anything written and read across runs -- cursors,
  caches, snapshots, user records): read and write it under ``DATA_DIR``
  (defined below), never a hardcoded ``data/.apps/task-status/`` at the
  call site. ``DATA_DIR`` defaults to ``data/.apps/task-status/`` but
  honors the ``TASK_STATUS_DATA_DIR`` env var, so an editing agent can point a
  throwaway instance at a *copy* of the data instead of the live store
  (see the update-app skill). Do NOT use ``Path(__file__)``-based
  paths for state -- the bug to avoid is one process writing to
  ``/home/user/workspace/data/.apps/...`` while another reads from
  ``/home/user/workspace/system/apps/<pkg>/data/...``.
- Static assets shipped alongside this file (templates, default
  configs, bundled JSON): ``Path(__file__).parent / "assets/..."`` is
  fine and is the right pattern.
- Listen port: bind ``PORT`` (defined below), which defaults to this
  app's assigned port but honors the ``TASK_STATUS_PORT`` env var, so
  an editing agent can boot a throwaway instance on a *spare* port
  alongside the live one (see the update-app skill). Never hardcode
  the port at the ``run_simple`` call.

This is a synchronous Flask app served by the threaded Werkzeug server.
The app owns its own browser origin (the forwarder routes
``http://task-status.<workspace-host>/`` straight to this port), so it serves
at ``/`` and root-absolute URLs, cookies, and service workers all work
unmodified -- nothing rewrites anything. Use ``flask_sock`` if you need
WebSockets.
"""

import json
import os
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, abort, send_file, stream_with_context
from markupsafe import escape
from werkzeug.serving import run_simple

# Persistent state for this app lives under DATA_DIR. It defaults to
# ``data/.apps/task-status/`` but is overridable via the ``TASK_STATUS_DATA_DIR`` env var
# so a throwaway instance can run against a *copy* of the data while editing --
# see the update-app skill. Always read/write state through DATA_DIR;
# never hardcode ``data/.apps/task-status/`` at a call site, or the override is
# bypassed. A writing call site should ``DATA_DIR.mkdir(parents=True,
# exist_ok=True)`` before writing.
DATA_DIR = Path(os.environ.get("TASK_STATUS_DATA_DIR", "data/.apps/task-status"))

# Listen port. Defaults to this app's assigned port but is overridable via
# the ``TASK_STATUS_PORT`` env var so an editing agent can boot a throwaway
# instance on a spare port next to the live one (see the update-app skill).
# Never hardcode the port at the ``run_simple`` call, or the override is bypassed.
PORT = int(os.environ.get("TASK_STATUS_PORT", "8095"))

# The browser-side modules the workspace shell builds and every app serves from
# its own origin: the app contract (how a page talks to the shell framing it) and
# the element context menu (the right-click menu whose last rows hand the
# clicked element to a chat). A module import is a fetch without cookies, which
# the forwarder refuses across origins, so they are served here rather than from
# the shell. Relative to the repo root the service runs from, like DATA_DIR.
SHELL_STATIC_MODULES_DIR = Path("system/apps/system_interface/imbue/system_interface/static/_static")
SHELL_STATIC_MODULE_NAMES = ('app_contract.js', 'context_menu.js')

# The script every page serves (keep it on every page): it connects the page to
# the shell, reports where the page is on the handshake so the shell can reopen
# this app's window at the same place, and installs the element context menu.
# A page visited outside the shell runs it harmlessly: nothing arrives, and the
# menu's Explain and Modify rows grey out.
SHELL_PAGE_SCRIPT = """<script type="module">
  import { connectToShell } from "/_static/app_contract.js";
  import { installElementContextMenu } from "/_static/context_menu.js";
  let handshake = null;
  const connection = connectToShell({
    onHandshake: (received) => {
      handshake = received;
      connection.location(location.pathname + location.search, document.title);
    },
  });
  installElementContextMenu({ connection, handshake: () => handshake });
</script>"""

# How often the live-update stream re-reads status.json, and how often it sends a
# keep-alive event when nothing changed (so tunnels and proxies keep it open and
# a closed viewer is noticed). It is a named event, not an SSE comment line: the
# ngrok tunnel holds back comment-only writes.
STATUS_POLL_SECONDS = 0.5
KEEPALIVE_SECONDS = 15.0

AGENT_ACTIVE_COLOR = "#22c55e"
AGENT_IDLE_COLOR = "#9ca3af"

# The "status" link is a self-reference to this same page, so clicking it to
# "open" it is pointless -- render it as a copy-to-clipboard button instead.
COPY_ICON_SVG = (
    '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>'
    '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>'
    "</svg>"
)

app = Flask("task_status", static_folder=None)


def load_status():
    status_file = DATA_DIR / "status.json"
    if not status_file.exists():
        return {
            "title": "No task loaded",
            "status": "Create status.json to get started",
            "links": {},
            "agent_active": False,
            "updated_at": None,
        }
    try:
        with open(status_file) as f:
            data = json.load(f)
        # The status's own last-edited time, not "now" -- read from the file's
        # mtime so it reflects when status.json actually last changed, and
        # formatted client-side so each viewer sees it in their own timezone.
        mtime = status_file.stat().st_mtime
        data["updated_at"] = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        return data
    except (json.JSONDecodeError, IOError):
        return {
            "title": "Error loading status",
            "status": "Check status.json for errors",
            "links": {},
            "agent_active": False,
            "updated_at": None,
        }


def safe_link(url: str) -> str | None:
    """Return the link if it is safe to put in an href, else None (no script URLs)."""
    if url.startswith(("http://", "https://", "/")):
        return url
    return None


@app.route("/api/status")
def api_status() -> Response:
    return Response(json.dumps(load_status()), mimetype="application/json")


def status_event_stream() -> Iterator[str]:
    """Send the current status, then again every time status.json changes."""
    last_sent = None
    last_write_at = 0.0
    while True:
        current = json.dumps(load_status())
        now = time.monotonic()
        if current != last_sent:
            last_sent = current
            last_write_at = now
            yield f"event: status\ndata: {current}\n\n"
        elif now - last_write_at >= KEEPALIVE_SECONDS:
            last_write_at = now
            yield "event: keep-alive\ndata: \n\n"
        time.sleep(STATUS_POLL_SECONDS)


@app.route("/events")
def events() -> Response:
    return Response(
        stream_with_context(status_event_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Re-renders the card in place from each live-update event, so every open copy of
# the card (here, or a viewer's app over the tunnel) shows a change as it lands.
LIVE_UPDATE_SCRIPT = """<script>
  const ACTIVE_COLOR = "%(active)s";
  const IDLE_COLOR = "%(idle)s";
  const COPY_ICON = %(copy_icon)s;
  function safeLink(url) {
    return /^(https?:\\/\\/|\\/)/.test(url) ? url : null;
  }
  // toLocaleTimeString with no locale argument uses the viewer's own browser
  // timezone, so this reads correctly for anyone who opens the card, not just
  // whichever timezone the server happens to run in.
  function formatTimestamp(iso) {
    if (!iso) return "";
    return new Date(iso).toLocaleTimeString([], {
      hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short",
    });
  }
  function makeCopyButton() {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "link link-copy";
    btn.dataset.link = "status";
    btn.innerHTML = COPY_ICON + '<span class="link-copy-label">Status</span>';
    return btn;
  }
  function render(status) {
    const active = Boolean(status.agent_active);
    document.getElementById("status-dot").style.background = active ? ACTIVE_COLOR : IDLE_COLOR;
    document.getElementById("agent-status").textContent = active ? "Active" : "Idle";
    document.getElementById("title").textContent = status.title || "Task Status";
    document.getElementById("status-text").textContent = status.status || "...";
    document.getElementById("timestamp").textContent = formatTimestamp(status.updated_at);
    const links = document.getElementById("links");
    links.replaceChildren();
    for (const [label, url] of Object.entries(status.links || {})) {
      if (label === "status") {
        links.append(makeCopyButton());
        continue;
      }
      const href = url && safeLink(url);
      if (!href) continue;
      const link = document.createElement("a");
      link.href = href;
      link.target = "_blank";
      link.rel = "noopener";
      link.className = "link";
      link.dataset.link = label;
      link.textContent = label.charAt(0).toUpperCase() + label.slice(1);
      links.append(link);
    }
  }
  // Format the server-rendered initial timestamp into the viewer's own timezone
  // right away, without waiting on the first live-update event.
  const timestampEl = document.getElementById("timestamp");
  if (timestampEl.dataset.updatedAt) {
    timestampEl.textContent = formatTimestamp(timestampEl.dataset.updatedAt);
  }
  // Event delegation on the container: survives links.replaceChildren() re-renders.
  document.getElementById("links").addEventListener("click", (event) => {
    const btn = event.target.closest(".link-copy");
    if (!btn) return;
    const label = btn.querySelector(".link-copy-label");
    navigator.clipboard.writeText(window.location.href).then(() => {
      if (!label) return;
      const original = label.textContent;
      label.textContent = "Copied";
      setTimeout(() => { label.textContent = original; }, 1200);
    }).catch(() => {
      if (!label) return;
      label.textContent = "Copy failed";
      setTimeout(() => { label.textContent = "Status"; }, 1200);
    });
  });
  const stream = new EventSource("/events");
  stream.addEventListener("status", (event) => render(JSON.parse(event.data)));
</script>""" % {"active": AGENT_ACTIVE_COLOR, "idle": AGENT_IDLE_COLOR, "copy_icon": json.dumps(COPY_ICON_SVG)}


@app.route("/")
def index() -> Response:
    status = load_status()
    agent_status_color = AGENT_ACTIVE_COLOR if status.get("agent_active") else AGENT_IDLE_COLOR
    agent_status_text = "Active" if status.get("agent_active") else "Idle"

    links_html = ""
    for label, url in status.get("links", {}).items():
        if label == "status":
            links_html += (
                '<button type="button" class="link link-copy" data-link="status">'
                f'{COPY_ICON_SVG}<span class="link-copy-label">Status</span></button>'
            )
            continue
        href = url and safe_link(url)
        if href:
            links_html += (
                f'<a href="{escape(href)}" target="_blank" rel="noopener" class="link" '
                f'data-link="{escape(label)}">{escape(label.title())}</a>'
            )

    html = f"""<!doctype html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Task Status</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #fafafa;
            padding: 16px;
        }}
        .card {{
            background: #f8f8f8;
            border-radius: 10px;
            padding: 16px;
            max-width: 900px;
            aspect-ratio: 5 / 3;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08), 0 1px 3px rgba(0,0,0,0.04);
            display: flex;
            flex-direction: column;
            gap: 10px;
            overflow: hidden;
        }}
        .header {{
            display: flex;
            align-items: flex-start;
            gap: 10px;
            flex-shrink: 0;
        }}
        .status-dot {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
            flex-shrink: 0;
            margin-top: 5px;
        }}
        .title {{
            font-size: 18px;
            font-weight: 600;
            color: #1f2937;
            margin: 0;
            line-height: 1.3;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }}
        .agent-status {{
            font-size: 11px;
            color: #9ca3af;
            text-transform: uppercase;
            letter-spacing: 0.4px;
        }}
        .status-text {{
            font-size: 14px;
            color: #374151;
            line-height: 1.4;
            flex: 1;
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }}
        .footer {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            flex-shrink: 0;
        }}
        .links {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .link {{
            display: inline-block;
            padding: 6px 12px;
            background: white;
            border: 1px solid #d1d5db;
            border-radius: 5px;
            font-size: 12px;
            color: #1f2937;
            text-decoration: none;
            transition: all 0.15s;
        }}
        .link:hover {{
            background: #f9fafb;
            border-color: #9ca3af;
        }}
        .link-copy {{
            font: inherit;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }}
        .timestamp {{
            font-size: 11px;
            color: #9ca3af;
            white-space: nowrap;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="header">
            <div class="status-dot" id="status-dot" style="background: {agent_status_color}"></div>
            <div style="flex: 1;">
                <h1 class="title" id="title">{escape(status.get("title", "Task Status"))}</h1>
                <div class="agent-status" id="agent-status">{agent_status_text}</div>
            </div>
        </div>
        <div class="status-text" id="status-text">{escape(status.get("status", "..."))}</div>
        <div class="footer">
            <div class="links" id="links">
                {links_html}
            </div>
            <div class="timestamp" id="timestamp" data-updated-at="{escape(status.get("updated_at") or "")}"></div>
        </div>
    </div>
    {LIVE_UPDATE_SCRIPT}
    {SHELL_PAGE_SCRIPT}
</body>
</html>"""
    return Response(html, mimetype="text/html")


@app.route("/_static/<basename>")
def shell_module(basename: str) -> Response:
    # The two shell-built modules and nothing else: a name that is not one of
    # them is a 404, so this route can never read outside that directory.
    if basename not in SHELL_STATIC_MODULE_NAMES:
        abort(404)
    module_path = SHELL_STATIC_MODULES_DIR / basename
    if not module_path.is_file():
        abort(404)
    # Flask resolves a relative path against the app's own directory, not the cwd.
    return send_file(module_path.absolute(), mimetype="text/javascript")


@app.route("/health")
def health() -> Response:
    return Response('{"status": "ok"}', mimetype="application/json")


def main() -> None:
    run_simple(
        "127.0.0.1", PORT, app, threaded=True, use_reloader=False, use_debugger=False
    )


if __name__ == "__main__":
    main()
