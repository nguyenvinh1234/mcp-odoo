"""Create isolated staging secrets without printing secret values."""

from __future__ import annotations

import json
import secrets
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SECRET_DIR = ROOT / "secrets"


def _new_secret() -> str:
    return secrets.token_urlsafe(36)


def _write_once(path: Path, value: str) -> None:
    if path.exists():
        raise SystemExit(f"Refusing to overwrite existing secret: {path}")
    path.write_text(value.rstrip() + "\n", encoding="utf-8")
    path.chmod(0o600)


def main() -> None:
    SECRET_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    postgres_password = _new_secret()
    oauth_client_secret = _new_secret()
    oauth_full_token = _new_secret()
    oauth_crm_token = _new_secret()
    odoo_full_password = _new_secret()
    odoo_crm_password = _new_secret()

    values = {
        "postgres_password.txt": postgres_password,
        "oauth_client_secret.txt": oauth_client_secret,
        "oauth_full_token.txt": oauth_full_token,
        "oauth_crm_token.txt": oauth_crm_token,
        "odoo_full_password.txt": odoo_full_password,
        "odoo_crm_password.txt": odoo_crm_password,
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
    client_policy = {
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
    _write_once(SECRET_DIR / "odoo_config.json", json.dumps(odoo_config, indent=2))
    _write_once(SECRET_DIR / "client_policy.json", json.dumps(client_policy, indent=2))

    env = "\n".join(
        [
            "COMPOSE_PROJECT_NAME=ngv-odoo-mcp-staging",
            "MCP_BIND_PORT=8765",
            "MCP_PUBLIC_HOST=127.0.0.1:8765",
            "MCP_RESOURCE_URL=http://127.0.0.1:8765/mcp",
            "MCP_AUTH_ISSUER_URL=http://oauth-stub:8080/",
            "MCP_AUTH_INTROSPECTION_URL=http://oauth-stub:8080/introspect",
            "",
        ]
    )
    _write_once(ROOT / ".env", env)
    print("Created staging secret files and .env; no secret values were printed.")


if __name__ == "__main__":
    main()
