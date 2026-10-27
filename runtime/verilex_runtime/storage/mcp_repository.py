from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.models import (
    DeleteMCPServerResult,
    MCPDiscoveredTool,
    MCPSecretRef,
    MCPServerRecord,
    MCPToolRecord,
)
from verilex_runtime.mcp.receipts import ToolDecision
from verilex_runtime.mcp.schema import validate_mcp_input_schema
from verilex_runtime.storage.database import Database

if TYPE_CHECKING:
    from verilex_runtime.mcp.secrets import MCPSecretManager, StagedSecretUpdate


class MCPRepository:
    def __init__(
        self,
        database: Database,
        *,
        secrets: MCPSecretManager | None = None,
    ):
        self._database = database
        self._secrets = secrets

    def create_server(
        self, record: MCPServerRecord, tools: Sequence[MCPDiscoveredTool]
    ) -> None:
        decisions = tuple(
            ToolDecision(
                tool.tool_name,
                True,
                self._tool_material(tool)[0],
                self._tool_material(tool)[2],
            )
            for tool in tools
        )
        with self._database.connect() as connection:
            self.create_server_on(connection, record, tools, decisions)

    def create_server_on(
        self,
        connection: sqlite3.Connection,
        record: MCPServerRecord,
        tools: Sequence[MCPDiscoveredTool],
        decisions: Sequence[ToolDecision],
    ) -> None:
        decision_map = self._validated_decisions(tools, decisions)
        connection.execute(
            """INSERT INTO mcp_servers (
                   id, display_name, transport, command, args_json, cwd, url,
                   env_json, headers_json, connect_timeout_ms,
                   request_timeout_ms, config_revision, config_fingerprint,
                   secret_revision, created_at, updated_at,
                   config_review_state, risk_findings_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            self._server_values(record),
        )
        discovered_at = self._utc_now()
        for tool in tools:
            self._insert_tool(
                connection,
                record.id,
                tool,
                enabled=decision_map[tool.tool_name].enabled,
                review_state="known",
                discovered_at=discovered_at,
            )

    def create_legacy_servers(
        self,
        items: Sequence[tuple[MCPServerRecord, StagedSecretUpdate | None]],
    ) -> None:
        """Insert a legacy batch and commit its secret journals atomically."""
        for record, staged in items:
            has_refs = bool(record.env_refs or record.header_refs)
            if (
                record.config_revision != 1
                or record.config_review_state not in ("needs_review", "blocked")
                or (record.config_review_state == "blocked" and not record.risk_findings)
            ):
                raise MCPDomainError("mcp_config_invalid")
            if staged is None:
                if has_refs or record.secret_revision != 0:
                    raise MCPDomainError("mcp_config_invalid")
                continue
            if (
                staged.operation.server_id != record.id
                or staged.operation.operation_kind != "migration"
                or not staged.operation.new_refs
                or not has_refs
                or record.secret_revision != 1
                or dict(staged.new_env_refs) != record.env_refs
                or dict(staged.new_header_refs) != record.header_refs
            ):
                raise MCPDomainError("mcp_config_invalid")
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for record, staged in items:
                connection.execute(
                    """INSERT INTO mcp_servers (
                           id, display_name, transport, command, args_json, cwd, url,
                           env_json, headers_json, connect_timeout_ms,
                           request_timeout_ms, config_revision, config_fingerprint,
                           secret_revision, created_at, updated_at,
                           config_review_state, risk_findings_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    self._server_values(record),
                )
                if staged is not None:
                    staged.commit_created_refs(connection)

    def get_server(self, server_id: str) -> MCPServerRecord | None:
        with self._database.connect() as connection:
            return self.get_server_on(connection, server_id)

    def get_server_on(
        self,
        connection: sqlite3.Connection,
        server_id: str,
    ) -> MCPServerRecord | None:
        row = connection.execute(
            "SELECT * FROM mcp_servers WHERE id = ?", (server_id,)
        ).fetchone()
        return None if row is None else self._server_record(row)

    def list_legacy_migration_servers(self) -> list[MCPServerRecord]:
        """Return records in the deterministic namespace reserved for migration."""
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM mcp_servers
                   WHERE substr(id, 1, 7) = 'legacy-'
                   ORDER BY created_at, id"""
            ).fetchall()
        return [self._server_record(row) for row in rows]

    def list_servers(self) -> list[MCPServerRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM mcp_servers ORDER BY created_at, id"
            ).fetchall()
        return [self._server_record(row) for row in rows]

    def selected_thread_counts(self) -> dict[str, int]:
        """Return a read-only, point-in-time selection count for every server."""
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT servers.id AS server_id,
                          COUNT(selections.thread_id) AS selected_thread_count
                   FROM mcp_servers AS servers
                   LEFT JOIN thread_mcp_servers AS selections
                     ON selections.server_id = servers.id
                   GROUP BY servers.id"""
            ).fetchall()
        return {
            str(row["server_id"]): int(row["selected_thread_count"])
            for row in rows
        }

    def update_server(
        self,
        server_id: str,
        patch: Mapping[str, object],
        expected_revision: int,
    ) -> bool:
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self.update_server_on(
                connection,
                server_id,
                patch,
                expected_revision,
            ) is not None

    def update_server_on(
        self,
        connection: sqlite3.Connection,
        server_id: str,
        patch: Mapping[str, object],
        expected_revision: int,
    ) -> MCPServerRecord | None:
        secret_fields = {"env_refs", "header_refs", "secret_revision"}
        if set(patch) & secret_fields:
            raise MCPDomainError("mcp_config_invalid")
        allowed = {
            "display_name",
            "transport",
            "command",
            "args",
            "cwd",
            "url",
            "env",
            "headers",
            "connect_timeout_ms",
            "request_timeout_ms",
            "config_fingerprint",
            "config_review_state",
            "risk_findings",
            "updated_at",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise ValueError(f"unsupported MCP server patch fields: {sorted(unknown)!r}")

        row = connection.execute(
            """SELECT * FROM mcp_servers
               WHERE id = ? AND config_revision = ?""",
            (server_id, expected_revision),
        ).fetchone()
        if row is None:
            return None

        current = self._server_record(row)
        changes = dict(patch)
        if "args" in changes:
            args = changes["args"]
            if not isinstance(args, (list, tuple)):
                raise TypeError("args must be a list or tuple of strings")
            changes["args"] = tuple(args)
        for field_name in ("env", "headers"):
            if field_name in changes:
                value = changes[field_name]
                if not isinstance(value, Mapping):
                    raise TypeError(f"{field_name} must be a mapping")
                changes[field_name] = dict(value)
        updated = replace(
            current,
            **changes,
            config_revision=current.config_revision + 1,
        )
        values = self._server_values(updated)
        cursor = connection.execute(
            """UPDATE mcp_servers
               SET display_name = ?, transport = ?, command = ?,
                   args_json = ?, cwd = ?, url = ?, env_json = ?,
                   headers_json = ?, connect_timeout_ms = ?,
                   request_timeout_ms = ?, config_revision = ?,
                   config_fingerprint = ?, secret_revision = ?,
                   created_at = ?, updated_at = ?,
                   config_review_state = ?, risk_findings_json = ?
               WHERE id = ? AND config_revision = ?""",
            (*values[1:], server_id, expected_revision),
        )
        return updated if cursor.rowcount == 1 else None

    def delete_server(self, server_id: str) -> DeleteMCPServerResult:
        if self._secrets is not None:
            record = self.get_server(server_id)
            if record is None:
                return DeleteMCPServerResult(False, 0)
            staged = self._secrets.stage_delete(
                server_id,
                current_env_refs=record.env_refs,
                current_header_refs=record.header_refs,
            )
            try:
                with self._database.connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    selected_thread_count = self._selected_thread_count_on(
                        connection,
                        server_id,
                    )
                    staged.commit_refs(
                        connection,
                        expected_revision=record.config_revision,
                    )
            except BaseException:
                staged.rollback_new_refs()
                raise
            try:
                staged.cleanup_old_refs()
            except Exception:
                # The database deletion above is already committed.  Leave the
                # committed journal entry for startup recovery instead of
                # reporting that the server still exists.
                pass
            return DeleteMCPServerResult(True, selected_thread_count)

        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record = self.get_server_on(connection, server_id)
            if record is None:
                return DeleteMCPServerResult(False, 0)
            if record.env_refs or record.header_refs:
                raise MCPDomainError("mcp_config_invalid")
            selected_thread_count = self._selected_thread_count_on(
                connection,
                server_id,
            )
            cursor = connection.execute(
                "DELETE FROM mcp_servers WHERE id = ?",
                (server_id,),
            )
            if cursor.rowcount != 1:
                raise MCPDomainError("mcp_revision_conflict")
        return DeleteMCPServerResult(True, selected_thread_count)

    def select_server(self, thread_id: str, server_id: str) -> None:
        with self._database.connect() as connection:
            self.select_server_on(connection, thread_id, server_id)

    def select_server_on(
        self,
        connection: sqlite3.Connection,
        thread_id: str,
        server_id: str,
    ) -> None:
        connection.execute(
            """INSERT INTO thread_mcp_servers (
                   thread_id, server_id, selected_at
               ) VALUES (?, ?, ?)
               ON CONFLICT(thread_id, server_id) DO NOTHING""",
            (thread_id, server_id, self._utc_now()),
        )

    def selected_at_on(
        self,
        connection: sqlite3.Connection,
        thread_id: str,
        server_id: str,
    ) -> str | None:
        row = connection.execute(
            """SELECT selected_at FROM thread_mcp_servers
               WHERE thread_id = ? AND server_id = ?""",
            (thread_id, server_id),
        ).fetchone()
        return None if row is None else str(row["selected_at"])

    def deselect_server(self, thread_id: str, server_id: str) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """DELETE FROM thread_mcp_servers
                   WHERE thread_id = ? AND server_id = ?""",
                (thread_id, server_id),
            )

    def selected_server_ids(self, thread_id: str) -> list[str]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT server_id FROM thread_mcp_servers
                   WHERE thread_id = ? ORDER BY selected_at, server_id""",
                (thread_id,),
            ).fetchall()
        return [row["server_id"] for row in rows]

    def replace_discovered_tools(
        self,
        server_id: str,
        tools: Sequence[MCPDiscoveredTool],
        *,
        initial_review: bool = False,
    ) -> None:
        decisions = tuple(
            ToolDecision(
                tool.tool_name,
                initial_review,
                self._tool_material(tool)[0],
                self._tool_material(tool)[2],
            )
            for tool in tools
        )
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.replace_discovered_tools_on(
                connection,
                server_id,
                tools,
                decisions,
                initial_review=initial_review,
            )

    def replace_discovered_tools_on(
        self,
        connection: sqlite3.Connection,
        server_id: str,
        tools: Sequence[MCPDiscoveredTool],
        decisions: Sequence[ToolDecision],
        *,
        initial_review: bool,
    ) -> None:
        decision_map = self._validated_decisions(tools, decisions)
        exists = connection.execute(
            "SELECT 1 FROM mcp_servers WHERE id = ?", (server_id,)
        ).fetchone()
        if exists is None:
            raise KeyError(server_id)

        discovered_at = self._utc_now()
        existing_rows = connection.execute(
            "SELECT * FROM mcp_server_tools WHERE server_id = ?",
            (server_id,),
        ).fetchall()
        existing = {row["tool_name"]: row for row in existing_rows}
        incoming_names: set[str] = set()
        for tool in tools:
            if tool.tool_name in incoming_names:
                raise ValueError("discovered tool names must be unique")
            incoming_names.add(tool.tool_name)
            description_hash, schema_json, schema_hash = self._tool_material(tool)
            current = existing.get(tool.tool_name)
            if current is None:
                self._insert_tool(
                    connection,
                    server_id,
                    tool,
                    enabled=(
                        decision_map[tool.tool_name].enabled
                        if initial_review
                        else False
                    ),
                    review_state="known" if initial_review else "new",
                    discovered_at=discovered_at,
                )
                continue

            changed = (
                current["description_hash"] != description_hash
                or current["schema_hash"] != schema_hash
            )
            if initial_review:
                enabled = decision_map[tool.tool_name].enabled
                review_state = "known"
            elif changed:
                enabled = False
                review_state = "changed"
            else:
                enabled = bool(current["enabled"])
                review_state = current["review_state"]
            connection.execute(
                """UPDATE mcp_server_tools
                   SET description = ?, description_hash = ?, schema_json = ?,
                       schema_hash = ?, enabled = ?, review_state = ?,
                       last_seen_at = ?
                   WHERE server_id = ? AND tool_name = ?""",
                (
                    tool.description,
                    description_hash,
                    schema_json,
                    schema_hash,
                    int(enabled),
                    review_state,
                    discovered_at,
                    server_id,
                    tool.tool_name,
                ),
            )

        for tool_name in set(existing) - incoming_names:
            connection.execute(
                """DELETE FROM mcp_server_tools
                   WHERE server_id = ? AND tool_name = ?""",
                (server_id, tool_name),
            )

    def mark_all_tools_changed_on(
        self,
        connection: sqlite3.Connection,
        server_id: str,
    ) -> None:
        connection.execute(
            """UPDATE mcp_server_tools
               SET enabled = 0, review_state = 'changed'
               WHERE server_id = ?""",
            (server_id,),
        )

    def list_tools(self, server_id: str) -> list[MCPToolRecord]:
        with self._database.connect() as connection:
            return self.list_tools_on(connection, server_id)

    def list_tools_on(self, connection: sqlite3.Connection, server_id: str) -> list[MCPToolRecord]:
        rows = connection.execute(
            """SELECT * FROM mcp_server_tools
               WHERE server_id = ? ORDER BY tool_name""",
            (server_id,),
        ).fetchall()
        return [self._tool_record(row) for row in rows]

    def review_tool(
        self,
        server_id: str,
        tool_name: str,
        enabled: bool,
        *,
        expected_description_hash: str,
        expected_schema_hash: str,
    ) -> bool:
        with self._database.connect() as connection:
            cursor = connection.execute(
                """UPDATE mcp_server_tools
                   SET enabled = ?, review_state = 'known'
                   WHERE server_id = ? AND tool_name = ?
                     AND description_hash = ? AND schema_hash = ?""",
                (
                    int(enabled),
                    server_id,
                    tool_name,
                    expected_description_hash,
                    expected_schema_hash,
                ),
            )
        return cursor.rowcount == 1

    @staticmethod
    def _selected_thread_count_on(
        connection: sqlite3.Connection,
        server_id: str,
    ) -> int:
        row = connection.execute(
            """SELECT COUNT(*) AS selected_thread_count
               FROM thread_mcp_servers WHERE server_id = ?""",
            (server_id,),
        ).fetchone()
        return int(row["selected_thread_count"])

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _settings_json(
        cls,
        values: Mapping[str, str],
        refs: Mapping[str, MCPSecretRef],
    ) -> str:
        overlap = set(values) & set(refs)
        if overlap:
            raise ValueError(f"plain and secret MCP settings overlap: {sorted(overlap)!r}")
        serialized: dict[str, object] = {}
        for key, value in values.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise TypeError("plain MCP settings must contain string keys and values")
            serialized[key] = value
        for key, secret_ref in refs.items():
            if not isinstance(key, str) or not isinstance(secret_ref, MCPSecretRef):
                raise TypeError("secret MCP settings must contain MCPSecretRef values")
            serialized[key] = {"$secret_ref": secret_ref.ref}
        return cls._canonical_json(serialized)

    @staticmethod
    def _settings_from_json(
        value: str,
    ) -> tuple[dict[str, str], dict[str, MCPSecretRef]]:
        try:
            parsed = json.loads(value)
            if not isinstance(parsed, dict):
                raise ValueError
            plain: dict[str, str] = {}
            refs: dict[str, MCPSecretRef] = {}
            for key, item in parsed.items():
                if not isinstance(key, str):
                    raise ValueError
                if isinstance(item, str):
                    plain[key] = item
                elif (
                    isinstance(item, dict)
                    and set(item) == {"$secret_ref"}
                    and isinstance(item["$secret_ref"], str)
                ):
                    refs[key] = MCPSecretRef(item["$secret_ref"])
                else:
                    raise ValueError
            return plain, refs
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise sqlite3.DatabaseError("invalid persisted MCP settings") from error

    @classmethod
    def _server_values(cls, record: MCPServerRecord) -> tuple[object, ...]:
        if not isinstance(record.args, tuple) or any(
            not isinstance(value, str) for value in record.args
        ):
            raise TypeError("MCP server args must be a tuple of strings")
        return (
            record.id,
            record.display_name,
            record.transport,
            record.command,
            cls._canonical_json(list(record.args)),
            record.cwd,
            record.url,
            cls._settings_json(record.env, record.env_refs),
            cls._settings_json(record.headers, record.header_refs),
            record.connect_timeout_ms,
            record.request_timeout_ms,
            record.config_revision,
            record.config_fingerprint,
            record.secret_revision,
            record.created_at,
            record.updated_at,
            record.config_review_state,
            cls._canonical_json(list(record.risk_findings)),
        )

    @classmethod
    def _server_record(cls, row: sqlite3.Row) -> MCPServerRecord:
        try:
            args = json.loads(row["args_json"])
            if not isinstance(args, list) or any(
                not isinstance(value, str) for value in args
            ):
                raise ValueError
            env, env_refs = cls._settings_from_json(row["env_json"])
            headers, header_refs = cls._settings_from_json(row["headers_json"])
            risk_findings = json.loads(row["risk_findings_json"])
            if not isinstance(risk_findings, list) or any(
                not isinstance(value, str) for value in risk_findings
            ):
                raise ValueError
            return MCPServerRecord(
                id=row["id"],
                display_name=row["display_name"],
                transport=row["transport"],
                command=row["command"],
                args=tuple(args),
                cwd=row["cwd"],
                url=row["url"],
                env=env,
                headers=headers,
                env_refs=env_refs,
                header_refs=header_refs,
                connect_timeout_ms=row["connect_timeout_ms"],
                request_timeout_ms=row["request_timeout_ms"],
                config_revision=row["config_revision"],
                config_fingerprint=row["config_fingerprint"],
                secret_revision=row["secret_revision"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                config_review_state=row["config_review_state"],
                risk_findings=tuple(risk_findings),
            )
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise sqlite3.DatabaseError("invalid persisted MCP server") from error

    @classmethod
    def _tool_material(cls, tool: MCPDiscoveredTool) -> tuple[str, str, str]:
        if not isinstance(tool.input_schema, dict):
            raise TypeError("MCP tool input schema must be an object")
        try:
            description_hash = hashlib.sha256(
                tool.description.encode("utf-8", "strict")
            ).hexdigest()
        except (AttributeError, TypeError, UnicodeError):
            raise MCPDomainError("mcp_schema_unsupported", retryable=False) from None
        canonical = validate_mcp_input_schema(tool.input_schema)
        schema_json = canonical.canonical_bytes.decode("utf-8")
        return (
            description_hash,
            schema_json,
            canonical.sha256,
        )

    @classmethod
    def _validated_decisions(
        cls,
        tools: Sequence[MCPDiscoveredTool],
        decisions: Sequence[ToolDecision],
    ) -> dict[str, ToolDecision]:
        tool_names: set[str] = set()
        for tool in tools:
            if tool.tool_name in tool_names:
                raise sqlite3.IntegrityError("discovered tool names must be unique")
            tool_names.add(tool.tool_name)
        decision_map: dict[str, ToolDecision] = {}
        for decision in decisions:
            if (
                not isinstance(decision, ToolDecision)
                or decision.tool_name in decision_map
            ):
                raise MCPDomainError("mcp_test_receipt_invalid")
            decision_map[decision.tool_name] = decision
        for tool in tools:
            description_hash, _schema_json, schema_hash = cls._tool_material(tool)
            decision = decision_map.get(tool.tool_name)
            if (
                decision is None
                or decision.expected_description_hash != description_hash
                or decision.expected_schema_hash != schema_hash
            ):
                raise MCPDomainError("mcp_test_receipt_invalid")
        if set(decision_map) != tool_names:
            raise MCPDomainError("mcp_test_receipt_invalid")
        return decision_map

    @classmethod
    def _insert_tool(
        cls,
        connection: sqlite3.Connection,
        server_id: str,
        tool: MCPDiscoveredTool,
        *,
        enabled: bool,
        review_state: str,
        discovered_at: str,
    ) -> None:
        description_hash, schema_json, schema_hash = cls._tool_material(tool)
        connection.execute(
            """INSERT INTO mcp_server_tools (
                   server_id, tool_name, description, description_hash,
                   schema_json, schema_hash, enabled, review_state,
                   first_seen_at, last_seen_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                server_id,
                tool.tool_name,
                tool.description,
                description_hash,
                schema_json,
                schema_hash,
                int(enabled),
                review_state,
                discovered_at,
                discovered_at,
            ),
        )

    @staticmethod
    def _tool_record(row: sqlite3.Row) -> MCPToolRecord:
        try:
            input_schema = json.loads(row["schema_json"])
            if not isinstance(input_schema, dict):
                raise ValueError
            canonical = validate_mcp_input_schema(input_schema)
            description_hash = hashlib.sha256(
                row["description"].encode("utf-8", "strict")
            ).hexdigest()
            if (
                canonical.sha256 != row["schema_hash"]
                or description_hash != row["description_hash"]
            ):
                raise ValueError
        except (
            json.JSONDecodeError,
            MCPDomainError,
            TypeError,
            UnicodeError,
            ValueError,
        ) as error:
            raise sqlite3.DatabaseError("invalid persisted MCP tool schema") from error
        return MCPToolRecord(
            server_id=row["server_id"],
            tool_name=row["tool_name"],
            description=row["description"],
            description_hash=row["description_hash"],
            input_schema=canonical.value,
            schema_hash=row["schema_hash"],
            enabled=bool(row["enabled"]),
            review_state=row["review_state"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
        )
