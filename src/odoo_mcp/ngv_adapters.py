"""Read-only Odoo adapters for the Ngoc Viet 16 -> 20 migration path."""

from __future__ import annotations

from typing import Any, cast

from .tool_helpers import parse_odoo_major_version


class ReadOnlyOdooAdapter:
    expected_major: int
    expected_transport: str

    def __init__(self, client: Any) -> None:
        self.client = client

    def assert_compatible(self) -> dict[str, Any]:
        transport = getattr(self.client, "transport", None)
        if transport != self.expected_transport:
            raise ValueError(
                f"Odoo {self.expected_major} adapter requires "
                f"{self.expected_transport}, got {transport or 'unknown'}"
            )
        version = self.client.get_server_version()
        candidates: list[Any] = []
        if isinstance(version, dict):
            version_info = version.get("server_version_info")
            if isinstance(version_info, (list, tuple)) and version_info:
                candidates.append(version_info[0])
            candidates.extend(
                [version.get("server_version"), version.get("server_serie")]
            )
        else:
            candidates.append(version)
        major = next(
            (
                parsed
                for candidate in candidates
                if (parsed := parse_odoo_major_version(candidate)) is not None
            ),
            None,
        )
        if major != self.expected_major:
            raise ValueError(
                f"Expected Odoo {self.expected_major}, backend reported {major or 'unknown'}"
            )
        return cast(dict[str, Any], version)

    def search(
        self,
        model: str,
        domain: list[Any],
        fields: list[str],
        *,
        limit: int,
        offset: int = 0,
        order: str = "id desc",
    ) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            self.client.search_read(
                model, domain, fields=fields, limit=limit, offset=offset, order=order
            ),
        )

    def count(self, model: str, domain: list[Any]) -> int:
        return int(self.client.execute_method(model, "search_count", domain))

    def group_by_assignee(self, model: str, domain: list[Any]) -> list[dict[str, Any]]:
        rows = self.client.execute_method(
            model, "read_group", domain, ["user_id"], ["user_id"], lazy=False
        )
        return list(rows or [])

    def whoami(self) -> dict[str, Any]:
        username = str(getattr(self.client, "username", ""))
        rows = self.search(
            "res.users",
            [["login", "=", username]],
            ["id", "name", "active", "company_id", "company_ids"],
            limit=1,
            order="id asc",
        )
        if not rows:
            raise PermissionError("Authenticated Odoo user is not readable")
        return rows[0]


class Odoo16ReadOnlyAdapter(ReadOnlyOdooAdapter):
    expected_major = 16
    expected_transport = "xmlrpc"


class Odoo20ReadOnlyAdapter(ReadOnlyOdooAdapter):
    expected_major = 20
    expected_transport = "json2"


def build_read_only_adapter(client: Any, expected_major: int) -> ReadOnlyOdooAdapter:
    if expected_major == 16:
        return Odoo16ReadOnlyAdapter(client)
    if expected_major == 20:
        return Odoo20ReadOnlyAdapter(client)
    raise ValueError("Only Odoo 16 and Odoo 20 adapters are supported")
