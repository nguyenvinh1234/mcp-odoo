"""Exercise authenticated HTTP body and subject/tool rate limits in staging."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx2


def _secret(name: str) -> str:
    return Path(f"/run/secrets/{name}").read_text(encoding="utf-8").strip()


def main() -> None:
    with httpx2.Client(timeout=20) as client:
        token_response = client.post(
            os.environ["MCP_AUTH_TOKEN_URL"],
            data={
                "grant_type": "password",
                "client_id": "ngv-odoo-mcp-staging",
                "client_secret": _secret("oauth_client_secret"),
                "username": "staging-writer-ai",
                "password": _secret("keycloak_writer_password"),
                "scope": "odoo.read odoo.write.staging",
            },
        )
        token_response.raise_for_status()
        token = token_response.json()["access_token"]
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }

        oversized = client.post(
            os.environ["MCP_WRITE_TEST_URL"],
            headers=headers,
            content=b"x" * 65537,
        )
        assert oversized.status_code == 413, oversized.text

        statuses: list[int] = []
        for request_id in range(1, 12):
            response = client.post(
                os.environ["MCP_WRITE_TEST_URL"],
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {
                        "name": "ngv_prepare_guard_probe",
                        "arguments": {},
                    },
                },
            )
            statuses.append(response.status_code)

        assert 429 not in statuses[:10], statuses
        assert statuses[10] == 429, statuses
        print(
            json.dumps(
                {
                    "http_guard_smoke": "PASS",
                    "authenticated_body_limit_status": oversized.status_code,
                    "rate_limit_status": statuses[10],
                    "rate_limit_requests_before_denial": 10,
                    "rate_key": "subject/tool",
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
