"""Create isolated staging secrets without printing secret values."""

from __future__ import annotations

import json
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SECRET_DIR = ROOT / "secrets"
FULL_SUBJECT = "11111111-1111-4111-8111-111111111111"
CRM_SUBJECT = "22222222-2222-4222-8222-222222222222"
REALM = "ngv-mcp-staging"
AUTH_PUBLIC_HOST = "auth-mcp-staging.ngocvietai.com"
MCP_PUBLIC_HOST = "mcp-staging.ngocvietai.com"


def _new_secret() -> str:
    return secrets.token_urlsafe(36)


def _write_once(path: Path, value: str, *, mode: int = 0o644) -> None:
    if path.exists():
        raise SystemExit(f"Refusing to overwrite existing secret: {path}")
    path.write_text(value.rstrip() + "\n", encoding="utf-8")
    path.chmod(mode)


def _backup_and_write(path: Path, value: str, *, mode: int = 0o600) -> None:
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(path, path.with_name(f"{path.name}.pre-keycloak-{stamp}"))
    path.write_text(value.rstrip() + "\n", encoding="utf-8")
    path.chmod(mode)


def _realm_payload(
    client_secret: str, full_password: str, crm_password: str
) -> dict[str, object]:
    resource_url = f"https://{MCP_PUBLIC_HOST}/mcp"
    scopes = [
        "odoo.read",
        "odoo.crm.read",
        "odoo.customers.read",
        "odoo.sales.read",
        "odoo.products.read",
    ]
    return {
        "realm": REALM,
        "enabled": True,
        "sslRequired": "external",
        "registrationAllowed": False,
        "resetPasswordAllowed": False,
        "bruteForceProtected": True,
        "accessTokenLifespan": 300,
        "ssoSessionIdleTimeout": 1800,
        "clientScopes": [
            {
                "name": scope,
                "protocol": "openid-connect",
                "attributes": {
                    "include.in.token.scope": "true",
                    "display.on.consent.screen": "true",
                },
            }
            for scope in scopes
        ],
        "clients": [
            {
                "clientId": "ngv-odoo-mcp-staging",
                "name": "Ngoc Viet Odoo MCP staging",
                "enabled": True,
                "clientAuthenticatorType": "client-secret",
                "secret": client_secret,
                "publicClient": False,
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": True,
                "serviceAccountsEnabled": False,
                "redirectUris": [
                    f"https://{MCP_PUBLIC_HOST}/oauth/callback"
                ],
                "webOrigins": [],
                "attributes": {
                    "pkce.code.challenge.method": "S256",
                    "oauth2.device.authorization.grant.enabled": "true",
                },
                "defaultClientScopes": scopes,
                "protocolMappers": [
                    {
                        "name": "mcp-resource-audience",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "consentRequired": False,
                        "config": {
                            "included.client.audience": "ngv-odoo-mcp-staging",
                            "id.token.claim": "false",
                            "access.token.claim": "true",
                            "introspection.token.claim": "true",
                        },
                    },
                    {
                        "name": "mcp-resource-url-audience",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "consentRequired": False,
                        "config": {
                            "included.custom.audience": resource_url,
                            "id.token.claim": "false",
                            "access.token.claim": "true",
                            "introspection.token.claim": "true",
                        },
                    },
                ],
            }
        ],
        "users": [
            {
                "id": FULL_SUBJECT,
                "username": "staging-full-ai",
                "enabled": True,
                "emailVerified": True,
                "email": "staging-full-ai@staging.invalid",
                "firstName": "Staging",
                "lastName": "Full AI",
                "credentials": [
                    {"type": "password", "value": full_password, "temporary": False}
                ],
            },
            {
                "id": CRM_SUBJECT,
                "username": "staging-crm-ai",
                "enabled": True,
                "emailVerified": True,
                "email": "staging-crm-ai@staging.invalid",
                "firstName": "Staging",
                "lastName": "CRM AI",
                "credentials": [
                    {"type": "password", "value": crm_password, "temporary": False}
                ],
            },
        ],
    }


def _client_policy() -> dict[str, object]:
    return {
        "version": 1,
        "clients": {
            "staging-full-ai": {
                "enabled": True,
                "instance": "odoo16-staging-full",
                "odoo_major": 16,
                "scopes": [
                    "odoo.read",
                    "odoo.crm.read",
                    "odoo.customers.read",
                    "odoo.sales.read",
                    "odoo.products.read",
                ],
                "tools": [
                    "ngv_whoami",
                    "ngv_list_leads",
                    "ngv_list_customers",
                    "ngv_list_quotations",
                    "ngv_list_products",
                    "ngv_daily_sales_summary",
                ],
            },
            "staging-crm-ai": {
                "enabled": True,
                "instance": "odoo16-staging-crm",
                "odoo_major": 16,
                "scopes": ["odoo.read", "odoo.crm.read", "odoo.customers.read"],
                "tools": ["ngv_whoami", "ngv_list_leads", "ngv_list_customers"],
            },
        },
    }


def _env_text() -> str:
    return "\n".join(
        [
            "COMPOSE_PROJECT_NAME=ngv-odoo-mcp-staging",
            "MCP_BIND_PORT=8765",
            f"MCP_PUBLIC_HOST={MCP_PUBLIC_HOST}",
            f"MCP_RESOURCE_URL=https://{MCP_PUBLIC_HOST}/mcp",
            f"MCP_AUTH_PUBLIC_HOST={AUTH_PUBLIC_HOST}",
            f"MCP_AUTH_ISSUER_URL=https://{AUTH_PUBLIC_HOST}/realms/{REALM}",
            (
                "MCP_AUTH_INTROSPECTION_URL="
                f"http://keycloak:8080/realms/{REALM}/protocol/openid-connect/token/introspect"
            ),
            (
                "MCP_AUTH_TOKEN_URL="
                f"http://keycloak:8080/realms/{REALM}/protocol/openid-connect/token"
            ),
            (
                "MCP_AUTH_REVOCATION_URL="
                f"http://keycloak:8080/realms/{REALM}/protocol/openid-connect/revoke"
            ),
            "",
        ]
    )


def main() -> None:
    upgrade = "--upgrade-keycloak" in sys.argv[1:]
    SECRET_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if upgrade:
        oauth_client_secret = (SECRET_DIR / "oauth_client_secret.txt").read_text(
            encoding="utf-8"
        ).strip()
        missing = {
            "keycloak_db_password.txt": _new_secret(),
            "keycloak_admin_password.txt": _new_secret(),
            "keycloak_full_password.txt": _new_secret(),
            "keycloak_crm_password.txt": _new_secret(),
        }
        for filename, value in missing.items():
            path = SECRET_DIR / filename
            if not path.exists():
                _write_once(path, value, mode=0o600)
        full_password = (SECRET_DIR / "keycloak_full_password.txt").read_text(
            encoding="utf-8"
        ).strip()
        crm_password = (SECRET_DIR / "keycloak_crm_password.txt").read_text(
            encoding="utf-8"
        ).strip()
        _backup_and_write(
            SECRET_DIR / "keycloak_realm.json",
            json.dumps(
                _realm_payload(oauth_client_secret, full_password, crm_password),
                indent=2,
            ),
        )
        _backup_and_write(
            SECRET_DIR / "client_policy.json",
            json.dumps(_client_policy(), indent=2),
        )
        _backup_and_write(ROOT / ".env", _env_text())
        print("Upgraded staging secrets for Keycloak; prior policy/env were backed up.")
        return

    postgres_password = _new_secret()
    oauth_client_secret = _new_secret()
    oauth_full_token = _new_secret()
    oauth_crm_token = _new_secret()
    odoo_full_password = _new_secret()
    odoo_crm_password = _new_secret()
    keycloak_db_password = _new_secret()
    keycloak_admin_password = _new_secret()
    keycloak_full_password = _new_secret()
    keycloak_crm_password = _new_secret()

    values = {
        "postgres_password.txt": postgres_password,
        "oauth_client_secret.txt": oauth_client_secret,
        "oauth_full_token.txt": oauth_full_token,
        "oauth_crm_token.txt": oauth_crm_token,
        "odoo_full_password.txt": odoo_full_password,
        "odoo_crm_password.txt": odoo_crm_password,
        "keycloak_db_password.txt": keycloak_db_password,
        "keycloak_admin_password.txt": keycloak_admin_password,
        "keycloak_full_password.txt": keycloak_full_password,
        "keycloak_crm_password.txt": keycloak_crm_password,
    }
    for filename, value in values.items():
        _write_once(SECRET_DIR / filename, value)

    odoo_config = {
        "default": "odoo16-staging-full",
        "instances": {
            "odoo16-staging-full": {
                "url": "http://odoo16:8069",
                "db": "ngv_mcp_staging",
                "username": "mcp-full@staging.invalid",
                "password": odoo_full_password,
                "transport": "xmlrpc",
            },
            "odoo16-staging-crm": {
                "url": "http://odoo16:8069",
                "db": "ngv_mcp_staging",
                "username": "mcp-crm@staging.invalid",
                "password": odoo_crm_password,
                "transport": "xmlrpc",
            },
        },
    }
    client_policy = _client_policy()
    _write_once(SECRET_DIR / "odoo_config.json", json.dumps(odoo_config, indent=2))
    _write_once(SECRET_DIR / "client_policy.json", json.dumps(client_policy, indent=2))

    _write_once(
        SECRET_DIR / "keycloak_realm.json",
        json.dumps(
            _realm_payload(
                oauth_client_secret, keycloak_full_password, keycloak_crm_password
            ),
            indent=2,
        ),
        mode=0o600,
    )
    _write_once(ROOT / ".env", _env_text(), mode=0o600)
    print("Created staging secret files and .env; no secret values were printed.")


if __name__ == "__main__":
    main()
