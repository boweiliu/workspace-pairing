#!/usr/bin/env python3
"""Signature gate in front of the task-status app, for visitors arriving over the tunnel.

The ngrok tunnel points here, never at the app itself, so everything that reaches this
port came from outside. A request is let through only when it carries an
``X-Status-Auth`` header signed by a key listed in the allowed-signers file:

    X-Status-Auth: <principal> <expires-unix-seconds> <signature>

``<signature>`` is the base64 body of an SSH signature (``ssh-keygen -Y sign -n
status-card``, armor lines and newlines removed) over the message
``status-card\\n<principal>\\n<expires>\\n``. The allowed-signers file is OpenSSH's own
format (one ``<principal> namespaces="status-card" ssh-ed25519 AAAA... comment`` line per
viewer), so adding a viewer is pasting their public key, and removing the line revokes
them on their next request.

Only GET and HEAD are forwarded; responses stream through unbuffered, so the app's
live-update stream reaches the viewer as it happens. While a stream is open, the gate
rechecks every ``REVOCATION_RECHECK_SECONDS`` that the viewer's principal is still in
the allowed-signers file, and cuts the connection the moment it isn't -- so revoking a
viewer stops an already-open stream too, not just their next request.

``POST /__pairing/accept`` is the one path handled locally instead of forwarded: it
lets a peer workspace redeem a single-use invite (see ``invites.py``,
``create_invite.py``) by posting its own public key, instead of the owner pasting a line
into ``allowed_signers`` by hand.

Standard library plus the ``ssh-keygen`` binary.
"""

from __future__ import annotations

import fcntl
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import invites

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = int(os.environ.get("STATUS_GATE_PORT", "8096"))
UPSTREAM_HOST = "127.0.0.1"
UPSTREAM_PORT = int(os.environ.get("STATUS_GATE_UPSTREAM_PORT", "8095"))
ALLOWED_SIGNERS = Path(os.environ.get("STATUS_GATE_ALLOWED_SIGNERS", "data/.apps/task-status/allowed_signers"))

NAMESPACE = "status-card"
AUTH_HEADER = "X-Status-Auth"
# A signed token may be valid for at most this long, so a leaked one expires soon.
MAX_TOKEN_LIFETIME_SECONDS = 3600
PRINCIPAL_PATTERN = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
SIGNATURE_PATTERN = re.compile(r"^[A-Za-z0-9+/=]{16,4096}$")
PUBLIC_KEY_PATTERN = re.compile(r"^(ssh-ed25519) ([A-Za-z0-9+/=]{16,1024})")
PAIRING_ACCEPT_PATH = "/__pairing/accept"
MAX_PAIRING_BODY_BYTES = 4096
# How often an open stream is rechecked against allowed_signers, in bytes-of-idle-time
# terms: the card's own keepalive fires every 15s, so this fires reliably on top of it.
REVOCATION_RECHECK_SECONDS = 30
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    AUTH_HEADER.lower(),
}
STREAM_CHUNK_BYTES = 65536


def signed_message(principal: str, expires: int) -> bytes:
    return f"{NAMESPACE}\n{principal}\n{expires}\n".encode()


def armor(signature_body: str) -> str:
    lines = [signature_body[index : index + 70] for index in range(0, len(signature_body), 70)]
    return "-----BEGIN SSH SIGNATURE-----\n" + "\n".join(lines) + "\n-----END SSH SIGNATURE-----\n"


class TokenVerifier:
    """Checks tokens against the allowed-signers file, caching successes until they expire.

    The cache is dropped whenever the allowed-signers file changes, so removing a
    viewer's line takes effect on their next request.
    """

    def __init__(self, allowed_signers: Path) -> None:
        self._allowed_signers = allowed_signers
        self._lock = threading.Lock()
        self._verified: dict[str, int] = {}
        self._signers_stamp: tuple[int, int] | None = None

    def _current_stamp(self) -> tuple[int, int] | None:
        try:
            stat = self._allowed_signers.stat()
        except FileNotFoundError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def check(self, header_value: str | None, now: int) -> tuple[str | None, str]:
        """Return (principal, "") when the token is good, else (None, reason)."""
        if not header_value:
            return None, f"missing {AUTH_HEADER} header"
        parts = header_value.split(" ")
        if len(parts) != 3:
            return None, f"malformed {AUTH_HEADER} header"
        principal, expires_text, signature = parts
        if not PRINCIPAL_PATTERN.match(principal) or not SIGNATURE_PATTERN.match(signature):
            return None, f"malformed {AUTH_HEADER} header"
        if not expires_text.isdigit():
            return None, "malformed expiry"
        expires = int(expires_text)
        if expires <= now:
            return None, "token expired"
        if expires - now > MAX_TOKEN_LIFETIME_SECONDS:
            return None, "token lifetime too long"
        stamp = self._current_stamp()
        if stamp is None:
            return None, "no viewers are allowed yet"
        with self._lock:
            if stamp != self._signers_stamp:
                self._verified.clear()
                self._signers_stamp = stamp
            self._verified = {key: until for key, until in self._verified.items() if until > now}
            if header_value in self._verified:
                return principal, ""
        if not self._verify_with_ssh_keygen(principal, expires, signature):
            return None, "signature not accepted"
        with self._lock:
            self._verified[header_value] = expires
        return principal, ""

    def principal_still_listed(self, principal: str) -> bool:
        """Re-read allowed_signers and check membership, ignoring the verified-token cache.

        Used to cut an already-open stream once its viewer is revoked -- the cache above
        exists to skip re-verifying a signature, not to skip noticing a removed line.
        """
        try:
            text = self._allowed_signers.read_text()
        except FileNotFoundError:
            return False
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.split(None, 1)[0] == principal:
                return True
        return False

    def _verify_with_ssh_keygen(self, principal: str, expires: int, signature: str) -> bool:
        with tempfile.NamedTemporaryFile("w", suffix=".sig", delete=True) as signature_file:
            signature_file.write(armor(signature))
            signature_file.flush()
            result = subprocess.run(
                [
                    "ssh-keygen",
                    "-Y",
                    "verify",
                    "-f",
                    str(self._allowed_signers),
                    "-I",
                    principal,
                    "-n",
                    NAMESPACE,
                    "-s",
                    signature_file.name,
                ],
                input=signed_message(principal, expires),
                capture_output=True,
                timeout=10,
            )
        return result.returncode == 0


VERIFIER = TokenVerifier(ALLOWED_SIGNERS)


def _looks_like_a_real_key(key_type: str, key_data: str) -> bool:
    """Confirm ssh-keygen itself accepts the key, beyond the regex shape check."""
    with tempfile.NamedTemporaryFile("w", suffix=".pub", delete=True) as key_file:
        key_file.write(f"{key_type} {key_data}\n")
        key_file.flush()
        result = subprocess.run(["ssh-keygen", "-l", "-f", key_file.name], capture_output=True, timeout=5)
    return result.returncode == 0


class PrincipalAlreadyPairedError(ValueError):
    """Raised when a pairing accept would overwrite an existing viewer's line."""


def append_allowed_signer(principal: str, key_type: str, key_data: str) -> None:
    """Add a viewer line, refusing to overwrite an existing principal.

    Only ``principal``, ``key_type``, and ``key_data`` -- each already validated by the
    caller -- end up in the file; nothing else from the request reaches it.
    """
    ALLOWED_SIGNERS.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ALLOWED_SIGNERS.with_suffix(".lock")
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            existing = ALLOWED_SIGNERS.read_text() if ALLOWED_SIGNERS.exists() else ""
            for line in existing.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and line.split(None, 1)[0] == principal:
                    raise PrincipalAlreadyPairedError(f"principal {principal!r} is already paired")
            with open(ALLOWED_SIGNERS, "a") as handle:
                handle.write(f'{principal} namespaces="{NAMESPACE}" {key_type} {key_data}\n')
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


class GateHandler(BaseHTTPRequestHandler):
    # HTTP/1.0 responses end when the connection closes, so a streamed body needs no
    # length and no chunking of our own.
    protocol_version = "HTTP/1.0"
    server_version = "status-gate"

    def do_GET(self) -> None:
        self._forward(send_body=True)

    def do_HEAD(self) -> None:
        self._forward(send_body=False)

    def _refuse_method(self) -> None:
        self._send_plain(405, "only GET and HEAD are allowed")

    def do_POST(self) -> None:
        if self.path == PAIRING_ACCEPT_PATH:
            self._handle_pairing_accept()
            return
        self._refuse_method()

    do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _refuse_method

    def _send_plain(self, status: int, message: str) -> None:
        body = (message + "\n").encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_pairing_accept(self) -> None:
        length_header = self.headers.get("Content-Length")
        if length_header is None or not length_header.isdigit():
            self._send_json(400, {"ok": False, "error": "missing Content-Length"})
            return
        length = int(length_header)
        if length > MAX_PAIRING_BODY_BYTES:
            self._send_json(413, {"ok": False, "error": "request too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "invalid JSON"})
            return
        code = payload.get("code") if isinstance(payload, dict) else None
        principal = payload.get("principal") if isinstance(payload, dict) else None
        public_key = payload.get("public_key") if isinstance(payload, dict) else None
        if not isinstance(code, str) or not isinstance(principal, str) or not isinstance(public_key, str):
            self._send_json(400, {"ok": False, "error": "code, principal, and public_key are required"})
            return
        if not PRINCIPAL_PATTERN.match(principal):
            self._send_json(400, {"ok": False, "error": "malformed principal"})
            return
        key_match = PUBLIC_KEY_PATTERN.match(public_key.strip())
        if not key_match:
            self._send_json(400, {"ok": False, "error": "malformed public_key: expected 'ssh-ed25519 <base64>'"})
            return
        key_type, key_data = key_match.group(1), key_match.group(2)
        if not _looks_like_a_real_key(key_type, key_data):
            self._send_json(400, {"ok": False, "error": "public_key is not a valid key"})
            return
        record, reason = invites.redeem(code)
        if record is None:
            self.log_message("pairing accept refused: %s", reason)
            self._send_json(403, {"ok": False, "error": reason})
            return
        if record["principal"] != principal:
            self.log_message("pairing accept refused: principal does not match invite")
            self._send_json(403, {"ok": False, "error": "principal does not match this invite"})
            return
        try:
            append_allowed_signer(principal, key_type, key_data)
        except ValueError as error:
            # The invite is already spent at this point (single-use), so a name collision
            # here means: mint a fresh invite with a different principal and retry.
            self._send_json(409, {"ok": False, "error": str(error)})
            return
        self.log_message("paired new viewer: %s", principal)
        self._send_json(200, {"ok": True, "principal": principal, "namespace": NAMESPACE})

    def _forward(self, send_body: bool) -> None:
        principal, reason = VERIFIER.check(self.headers.get(AUTH_HEADER), int(time.time()))
        if principal is None:
            self.log_message("refused %s: %s", self.path, reason)
            self._send_plain(401, f"not authorized: {reason}")
            return
        outgoing_headers = {
            name: value for name, value in self.headers.items() if name.lower() not in HOP_BY_HOP_HEADERS
        }
        outgoing_headers["X-Status-Viewer"] = principal
        connection = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=30)
        headers_sent = False
        try:
            connection.request(self.command, self.path, headers=outgoing_headers)
            upstream = connection.getresponse()
            # A live-update stream stays open indefinitely between events.
            if connection.sock is not None:
                connection.sock.settimeout(None)
            headers_sent = True
            self.send_response(upstream.status, upstream.reason)
            for name, value in upstream.getheaders():
                if name.lower() not in HOP_BY_HOP_HEADERS:
                    self.send_header(name, value)
            self.end_headers()
            if not send_body:
                return
            last_recheck = time.monotonic()
            for chunk in iter(lambda: upstream.read1(STREAM_CHUNK_BYTES), b""):
                self.wfile.write(chunk)
                self.wfile.flush()
                now_monotonic = time.monotonic()
                if now_monotonic - last_recheck >= REVOCATION_RECHECK_SECONDS:
                    last_recheck = now_monotonic
                    if not VERIFIER.principal_still_listed(principal):
                        self.log_message("revoked mid-stream: %s", principal)
                        break
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError as error:
            self.log_message("upstream error on %s: %s", self.path, error)
            if not headers_sent:
                self._send_plain(502, "the status card is not reachable right now")
        finally:
            connection.close()


def main() -> int:
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), GateHandler)
    server.daemon_threads = True
    print(f"status gate on {LISTEN_HOST}:{LISTEN_PORT} -> {UPSTREAM_HOST}:{UPSTREAM_PORT}", file=sys.stderr, flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
