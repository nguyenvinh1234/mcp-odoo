"""Private RFC 7662 test introspection endpoint for isolated staging only."""

from __future__ import annotations

import base64
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs


def _secret(name: str) -> str:
    return Path(f"/run/secrets/{name}").read_text(encoding="utf-8").strip()


CLIENT_ID = os.environ["OAUTH_CLIENT_ID"]
CLIENT_SECRET = _secret("oauth_client_secret")
ISSUER = os.environ["OAUTH_ISSUER_URL"]
RESOURCE = os.environ["OAUTH_RESOURCE_URL"]
TOKENS = {
    _secret("oauth_full_token"): {
        "sub": "staging-full-ai",
        "scope": "odoo.read odoo.crm.read odoo.customers.read odoo.sales.read odoo.products.read",
    },
    _secret("oauth_crm_token"): {
        "sub": "staging-crm-ai",
        "scope": "odoo.read odoo.crm.read odoo.customers.read",
    },
}


class Handler(BaseHTTPRequestHandler):
    server_version = "ngv-oauth-staging/1"

    def log_message(self, format: str, *args: object) -> None:
        # Do not log bearer tokens or request bodies.
        return

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/introspect":
            self._json(404, {"error": "not_found"})
            return
        expected = "Basic " + base64.b64encode(
            f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
        ).decode()
        supplied = self.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied, expected):
            self._json(401, {"error": "invalid_client"})
            return
        length = min(int(self.headers.get("Content-Length", "0")), 8192)
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        token = form.get("token", [""])[0]
        identity = next(
            (value for key, value in TOKENS.items() if hmac.compare_digest(token, key)),
            None,
        )
        if identity is None:
            self._json(200, {"active": False})
            return
        self._json(
            200,
            {
                "active": True,
                "client_id": CLIENT_ID,
                "sub": identity["sub"],
                "scope": identity["scope"],
                "iss": ISSUER,
                "aud": RESOURCE,
                "exp": int(time.time()) + 3600,
            },
        )


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
