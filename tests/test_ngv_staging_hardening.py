from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_write_gateway_is_not_attached_to_shared_proxy_network():
    text = (ROOT / "deploy/staging/compose.yml").read_text(encoding="utf-8")
    section = text.split("  gateway-write:\n", 1)[1].split("\n  smoke:\n", 1)[0]
    assert "networks: [backend]" in section
    assert "proxy:" not in section
    assert "ports:" not in section
    assert "gateway-write-audit:/var/log/odoo-mcp" in section


def test_write_gateway_has_p0_http_guard_settings():
    text = (ROOT / "deploy/staging/compose.yml").read_text(encoding="utf-8")
    section = text.split("  gateway-write:\n", 1)[1].split("\n  smoke:\n", 1)[0]
    for name in (
        "ODOO_MCP_NGV_HTTP_MAX_BODY_BYTES",
        "ODOO_MCP_NGV_HTTP_TIMEOUT_SECONDS",
        "ODOO_MCP_NGV_RATE_READ_MAX",
        "ODOO_MCP_NGV_RATE_PREPARE_MAX",
        "ODOO_MCP_NGV_RATE_EXECUTE_MAX",
        "ODOO_MCP_NGV_CONCURRENCY_WRITE",
        "ODOO_MCP_AUDIT_MAX_BYTES",
        "ODOO_MCP_AUDIT_BACKUP_COUNT",
    ):
        assert name in section


def test_http_guard_smoke_covers_body_and_rate_limits():
    text = (ROOT / "deploy/staging/compose.yml").read_text(encoding="utf-8")
    assert "  http-guard-smoke:" in text
    smoke = (ROOT / "deploy/staging/smoke_http_guards.py").read_text(
        encoding="utf-8"
    )
    assert "413" in smoke
    assert "429" in smoke


def test_pkce_client_is_public_s256_without_password_grant():
    text = (ROOT / "deploy/staging/prepare_secrets.py").read_text(encoding="utf-8")
    section = text.split('"clientId": "ngv-odoo-mcp-staging-write"', 1)[1]
    assert '"publicClient": True' in section
    assert '"standardFlowEnabled": True' in section
    assert '"directAccessGrantsEnabled": False' in section
    assert '"pkce.code.challenge.method": "S256"' in section


def test_restore_drill_uses_dedicated_project_and_keeps_volumes():
    runner = (ROOT / "deploy/staging/run_restore_verify.sh").read_text(
        encoding="utf-8"
    )
    assert "-p ngv-odoo-mcp-restore-verify" in runner
    assert "--remove-orphans" not in runner
    assert " down" not in runner
    assert "down -v" not in runner
    assert "down --volumes" not in runner
    assert "RESTORE_RUN_ID" in runner

    compose = (ROOT / "deploy/staging/restore-verify.yml").read_text(
        encoding="utf-8"
    )
    assert "restore-backend: {internal: true}" in compose
    assert "ports:" not in compose
    assert "RESTORE_RUN_ID" in compose

    restore_script = (ROOT / "deploy/staging/restore_verify.sh").read_text(
        encoding="utf-8"
    )
    assert "SET transaction_timeout = 0" in restore_script
    assert "ON_ERROR_STOP=1" in restore_script
