"""Run a real Authorization Code + PKCE S256 flow against staging Keycloak."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import os
import secrets
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


CLIENT_ID = "ngv-odoo-mcp-staging-write"
RESOURCE_CLIENT_ID = "ngv-odoo-mcp-staging"
REDIRECT_URI = os.environ.get(
    "MCP_WRITE_OAUTH_REDIRECT_URI", "http://127.0.0.1:8767/callback"
)
SCOPES = "openid odoo.read odoo.crm.read odoo.customers.read odoo.sales.read odoo.products.read odoo.write.staging"


def _secret(name: str) -> str:
    return Path(f"/run/secrets/{name}").read_text(encoding="utf-8").strip()


class _LoginForm(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.action = ""
        self.inputs: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form" and not self.action:
            self.action = html.unescape(values.get("action", ""))
        if tag == "input" and values.get("name"):
            self.inputs[values["name"]] = values.get("value", "")


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def _whoami(token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=30) as http_client:
        async with streamable_http_client(
            os.environ["MCP_WRITE_TEST_URL"], http_client=http_client
        ) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                result = await session.call_tool("ngv_whoami", {})
                value = getattr(result, "structuredContent", None) or getattr(
                    result, "structured_content", None
                )
                assert isinstance(value, dict)
                return value


def main() -> None:
    issuer_internal = os.environ["MCP_AUTH_INTERNAL_ISSUER_URL"]
    authorize_url = f"{issuer_internal}/protocol/openid-connect/auth"
    token_url = f"{issuer_internal}/protocol/openid-connect/token"
    introspection_url = f"{issuer_internal}/protocol/openid-connect/token/introspect"
    revocation_url = f"{issuer_internal}/protocol/openid-connect/revoke"
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(18)
    query = urlencode(
        {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    with httpx2.Client(timeout=20, follow_redirects=False) as client:
        login = client.get(f"{authorize_url}?{query}")
        login.raise_for_status()
        parser = _LoginForm()
        parser.feed(login.text)
        assert parser.action
        form = parser.inputs
        form.update(
            {
                "username": "staging-writer-ai",
                "password": _secret("keycloak_writer_password"),
            }
        )
        login_action = urljoin(str(login.url), parser.action)
        parsed_action = urlparse(login_action)
        internal_issuer = urlparse(issuer_internal)
        if parsed_action.netloc != internal_issuer.netloc:
            login_action = parsed_action._replace(
                scheme=internal_issuer.scheme,
                netloc=internal_issuer.netloc,
            ).geturl()
        authenticated = client.post(login_action, data=form)
        assert authenticated.status_code in {302, 303}
        location = authenticated.headers["location"]
        parsed = urlparse(location)
        params = parse_qs(parsed.query)
        assert params.get("state") == [state]
        code = params["code"][0]
        token_response = client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "code": code,
                "code_verifier": verifier,
            },
        )
        token_response.raise_for_status()
        token = token_response.json()["access_token"]
        introspection = client.post(
            introspection_url,
            data={"token": token},
            auth=(RESOURCE_CLIENT_ID, _secret("oauth_client_secret")),
        )
        introspection.raise_for_status()
        data = introspection.json()
        assert data.get("active") is True
        assert data.get("username") == "staging-writer-ai"
        audiences = data.get("aud")
        audiences = audiences if isinstance(audiences, list) else [audiences]
        assert os.environ["MCP_WRITE_RESOURCE_URL"] in audiences
        assert "odoo.write.staging" in str(data.get("scope", "")).split()

        whoami = asyncio.run(_whoami(token))
        assert whoami.get("client_id") == "staging-writer-ai"

        revoked = client.post(
            revocation_url,
            data={"token": token, "token_type_hint": "access_token", "client_id": CLIENT_ID},
        )
        revoked.raise_for_status()
        after = client.post(
            introspection_url,
            data={"token": token},
            auth=(RESOURCE_CLIENT_ID, _secret("oauth_client_secret")),
        )
        after.raise_for_status()
        assert after.json().get("active") is False

    print(
        json.dumps(
            {
                "pkce_smoke": "PASS",
                "flow": "authorization_code",
                "pkce": "S256",
                "client_id": CLIENT_ID,
                "subject": "staging-writer-ai",
                "whoami": True,
                "audience_bound": True,
                "revoked_inactive": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
