"""Exercise real OAuth token issuance, introspection, and revocation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx2


CLIENT_ID = "ngv-odoo-mcp-staging"
SCOPES = {
    "odoo.read",
    "odoo.crm.read",
    "odoo.customers.read",
    "odoo.sales.read",
    "odoo.products.read",
}


def _secret(name: str) -> str:
    return Path(f"/run/secrets/{name}").read_text(encoding="utf-8").strip()


def _post(
    client: httpx2.Client,
    url: str,
    data: dict[str, str],
    *,
    auth: tuple[str, str] | None = None,
) -> dict[str, Any]:
    response = client.post(url, data=data, auth=auth)
    if response.is_error:
        try:
            failure = response.json()
        except ValueError:
            failure = {"error": f"HTTP {response.status_code}"}
        safe_failure = {
            key: failure.get(key)
            for key in ("error", "error_description")
            if isinstance(failure, dict) and failure.get(key)
        }
        raise AssertionError(f"OAuth request failed: {safe_failure}")
    if not response.content:
        return {}
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def main() -> None:
    token_url = os.environ["MCP_AUTH_TOKEN_URL"]
    introspection_url = os.environ["MCP_AUTH_INTROSPECTION_URL"]
    revocation_url = os.environ["MCP_AUTH_REVOCATION_URL"]
    expected_issuer = os.environ["MCP_AUTH_ISSUER_URL"]
    expected_audience = os.environ["MCP_RESOURCE_URL"]
    client_secret = _secret("oauth_client_secret")
    results: list[dict[str, Any]] = []

    with httpx2.Client(timeout=20) as client:
        subjects: set[str] = set()
        for username, password_secret in (
            ("staging-full-ai", "keycloak_full_password"),
            ("staging-crm-ai", "keycloak_crm_password"),
        ):
            token_payload = _post(
                client,
                token_url,
                {
                    "grant_type": "password",
                    "client_id": CLIENT_ID,
                    "client_secret": client_secret,
                    "username": username,
                    "password": _secret(password_secret),
                    "scope": " ".join(sorted(SCOPES)),
                },
            )
            token = token_payload.get("access_token")
            assert isinstance(token, str) and token
            introspected = _post(
                client,
                introspection_url,
                {"token": token},
                auth=(CLIENT_ID, client_secret),
            )
            assert introspected.get("active") is True
            assert introspected.get("username") == username
            subject = introspected.get("sub")
            if isinstance(subject, str) and subject:
                subjects.add(subject)
            assert introspected.get("iss") == expected_issuer
            audience = introspected.get("aud")
            audiences = audience if isinstance(audience, list) else [audience]
            assert expected_audience in audiences
            granted = set(str(introspected.get("scope", "")).split())
            assert SCOPES.issubset(granted)

            revoke = client.post(
                revocation_url,
                data={"token": token, "token_type_hint": "access_token"},
                auth=(CLIENT_ID, client_secret),
            )
            revoke.raise_for_status()
            after = _post(
                client,
                introspection_url,
                {"token": token},
                auth=(CLIENT_ID, client_secret),
            )
            assert after.get("active") is False
            results.append(
                {
                    "username": username,
                    "subject_present": True,
                    "issuer": introspected.get("iss"),
                    "audience_bound": True,
                    "scope_count": len(SCOPES),
                    "revoked_inactive": True,
                }
            )

        assert len(subjects) in {0, 2}

    print(json.dumps({"oauth_smoke": "PASS", "accounts": results}, indent=2))


if __name__ == "__main__":
    main()
