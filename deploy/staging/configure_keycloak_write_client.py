"""Idempotently configure the staging-only public PKCE write client."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx2


REALM = "ngv-mcp-staging"
CLIENT_ID = "ngv-odoo-mcp-staging-write"
KEYCLOAK = os.environ.get("KEYCLOAK_INTERNAL_URL", "http://keycloak:8080")
REDIRECT_URI = os.environ.get(
    "MCP_WRITE_OAUTH_REDIRECT_URI", "http://127.0.0.1:8767/callback"
)
WRITE_RESOURCE = os.environ["MCP_WRITE_RESOURCE_URL"]
SCOPES = [
    "profile",
    "odoo.read",
    "odoo.crm.read",
    "odoo.customers.read",
    "odoo.sales.read",
    "odoo.products.read",
    "odoo.write.staging",
]


def _secret(name: str) -> str:
    return Path(f"/run/secrets/{name}").read_text(encoding="utf-8").strip()


def main() -> None:
    with httpx2.Client(timeout=20) as client:
        token_response = client.post(
            f"{KEYCLOAK}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": "ngv-staging-admin",
                "password": _secret("keycloak_admin_password"),
            },
        )
        token_response.raise_for_status()
        token = token_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        base = f"{KEYCLOAK}/admin/realms/{REALM}"
        representation = {
            "clientId": CLIENT_ID,
            "name": "Ngoc Viet Odoo MCP staging write PKCE",
            "enabled": True,
            "publicClient": True,
            "clientAuthenticatorType": "client-secret",
            "standardFlowEnabled": True,
            "implicitFlowEnabled": False,
            "directAccessGrantsEnabled": False,
            "serviceAccountsEnabled": False,
            "authorizationServicesEnabled": False,
            "fullScopeAllowed": False,
            "consentRequired": False,
            "redirectUris": [REDIRECT_URI],
            "webOrigins": [],
            "attributes": {
                "pkce.code.challenge.method": "S256",
                "post.logout.redirect.uris": REDIRECT_URI,
            },
            "defaultClientScopes": SCOPES,
            "optionalClientScopes": [],
        }
        found = client.get(
            f"{base}/clients", params={"clientId": CLIENT_ID}, headers=headers
        )
        found.raise_for_status()
        rows = found.json()
        if rows:
            client_uuid = rows[0]["id"]
            updated = client.put(
                f"{base}/clients/{client_uuid}", json=representation, headers=headers
            )
            updated.raise_for_status()
            action = "updated"
        else:
            created = client.post(f"{base}/clients", json=representation, headers=headers)
            created.raise_for_status()
            location = created.headers["location"]
            client_uuid = location.rstrip("/").rsplit("/", 1)[-1]
            action = "created"

        scopes_response = client.get(f"{base}/client-scopes", headers=headers)
        scopes_response.raise_for_status()
        scope_ids = {row["name"]: row["id"] for row in scopes_response.json()}
        created_scopes: list[str] = []
        for name in SCOPES:
            if name == "profile" or name in scope_ids:
                continue
            created_scope = client.post(
                f"{base}/client-scopes",
                json={
                    "name": name,
                    "protocol": "openid-connect",
                    "attributes": {
                        "include.in.token.scope": "true",
                        "display.on.consent.screen": "true",
                    },
                },
                headers=headers,
            )
            created_scope.raise_for_status()
            created_scopes.append(name)

        if created_scopes:
            scopes_response = client.get(f"{base}/client-scopes", headers=headers)
            scopes_response.raise_for_status()
            scope_ids = {row["name"]: row["id"] for row in scopes_response.json()}

        missing = sorted(set(SCOPES) - set(scope_ids))
        if missing:
            raise RuntimeError(f"Required Keycloak client scopes are missing: {missing}")
        for name in SCOPES:
            response = client.put(
                f"{base}/clients/{client_uuid}/default-client-scopes/{scope_ids[name]}",
                headers=headers,
            )
            response.raise_for_status()

        assigned_response = client.get(
            f"{base}/clients/{client_uuid}/default-client-scopes", headers=headers
        )
        assigned_response.raise_for_status()
        assigned = {row["name"] for row in assigned_response.json()}
        missing_assigned = sorted(set(SCOPES) - assigned)
        if missing_assigned:
            raise RuntimeError(
                f"Required scopes were not assigned to the write client: {missing_assigned}"
            )

        mapper = {
            "name": "mcp-write-resource-url-audience",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-audience-mapper",
            "consentRequired": False,
            "config": {
                "included.custom.audience": WRITE_RESOURCE,
                "id.token.claim": "false",
                "access.token.claim": "true",
                "introspection.token.claim": "true",
            },
        }
        mapper_url = f"{base}/clients/{client_uuid}/protocol-mappers/models"
        existing = client.get(mapper_url, headers=headers)
        existing.raise_for_status()
        matched = next(
            (row for row in existing.json() if row.get("name") == mapper["name"]),
            None,
        )
        if matched:
            response = client.put(
                f"{mapper_url}/{matched['id']}",
                json={**mapper, "id": matched["id"]},
                headers=headers,
            )
        else:
            response = client.post(mapper_url, json=mapper, headers=headers)
        response.raise_for_status()

    print(
        json.dumps(
            {
                "keycloak_write_client": "PASS",
                "action": action,
                "client_id": CLIENT_ID,
                "public_client": True,
                "standard_flow": True,
                "direct_access_grants": False,
                "pkce": "S256",
                "redirect_uri": REDIRECT_URI,
                "required_scope_count": len(SCOPES),
                "created_scopes": created_scopes,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
