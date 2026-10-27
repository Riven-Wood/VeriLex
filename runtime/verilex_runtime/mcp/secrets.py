from __future__ import annotations

import hmac
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Literal
from uuid import uuid4

from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.models import (
    MCPSecretOperation,
    MCPSecretOperationKind,
    MCPSecretRef,
)
from verilex_runtime.secrets.base import SecretStore
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.mcp_repository import MCPRepository


SecretPatch = Mapping[str, str | None] | None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_refs(refs: tuple[str, ...]) -> str:
    return json.dumps(list(refs), separators=(",", ":"), sort_keys=True)


def _unique_refs(refs: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(refs))


def _encoded_ref_segment(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise MCPDomainError("mcp_config_invalid")
    return value.replace("%", "%25").replace(":", "%3A")


def _secret_ref_prefix(
    server_id: str,
    namespace: Literal["env", "header"],
    field_name: str,
) -> str:
    return (
        f"mcp:{_encoded_ref_segment(server_id)}:{namespace}:"
        f"{_encoded_ref_segment(field_name)}:"
    )


@dataclass(frozen=True, slots=True)
class SecretCleanupReport:
    completed_operations: int
    pending_operations: int


class MCPSecretJournal:
    def __init__(self, database: Database):
        self._database = database

    @property
    def database(self) -> Database:
        return self._database

    def _create(
        self,
        *,
        server_id: str,
        operation_kind: MCPSecretOperationKind,
        new_refs: tuple[str, ...],
        old_refs: tuple[str, ...],
    ) -> MCPSecretOperation:
        operation = MCPSecretOperation(
            id=str(uuid4()),
            server_id=server_id,
            new_refs=new_refs,
            old_refs=old_refs,
            state="staged",
            operation_kind=operation_kind,
        )
        timestamp = _utc_now()
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO mcp_secret_operations (
                       id, server_id, operation_kind, new_refs_json, old_refs_json,
                       state, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    operation.id,
                    operation.server_id,
                    operation.operation_kind,
                    _canonical_refs(operation.new_refs),
                    _canonical_refs(operation.old_refs),
                    operation.state,
                    timestamp,
                    timestamp,
                ),
            )
        return operation

    def get(
        self,
        operation_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> MCPSecretOperation | None:
        if connection is None:
            with self._database.connect() as owned:
                return self.get(operation_id, connection=owned)
        row = connection.execute(
            "SELECT * FROM mcp_secret_operations WHERE id = ?",
            (operation_id,),
        ).fetchone()
        return None if row is None else self._operation(row)

    def list_pending(self) -> list[MCPSecretOperation]:
        operations, _ = self._list_pending_isolated()
        return operations

    def _list_pending_isolated(
        self,
    ) -> tuple[list[MCPSecretOperation], int]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM mcp_secret_operations
                   ORDER BY created_at, id"""
            ).fetchall()
        operations: list[MCPSecretOperation] = []
        invalid_count = 0
        for row in rows:
            try:
                operations.append(self._operation(row))
            except sqlite3.DatabaseError:
                invalid_count += 1
        return operations, invalid_count

    def mark_state(
        self,
        operation_id: str,
        state: Literal["committed", "cleanup_pending"],
        *,
        connection: sqlite3.Connection | None = None,
        expected_state: str | None = None,
    ) -> None:
        if connection is None:
            with self._database.connect() as owned:
                self.mark_state(
                    operation_id,
                    state,
                    connection=owned,
                    expected_state=expected_state,
                )
            return
        sql = "UPDATE mcp_secret_operations SET state = ?, updated_at = ? WHERE id = ?"
        values: tuple[object, ...] = (state, _utc_now(), operation_id)
        if expected_state is not None:
            sql += " AND state = ?"
            values += (expected_state,)
        cursor = connection.execute(sql, values)
        if cursor.rowcount != 1:
            raise sqlite3.DatabaseError("invalid MCP secret journal transition")

    def delete(
        self,
        operation_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        if connection is None:
            with self._database.connect() as owned:
                self.delete(operation_id, connection=owned)
            return
        connection.execute(
            "DELETE FROM mcp_secret_operations WHERE id = ?",
            (operation_id,),
        )

    @staticmethod
    def _operation(row: sqlite3.Row) -> MCPSecretOperation:
        try:
            new_refs = json.loads(row["new_refs_json"])
            old_refs = json.loads(row["old_refs_json"])
            if (
                not isinstance(new_refs, list)
                or not isinstance(old_refs, list)
                or any(not isinstance(value, str) for value in new_refs)
                or any(not isinstance(value, str) for value in old_refs)
                or not isinstance(row["id"], str)
                or not row["id"]
                or not isinstance(row["server_id"], str)
                or not row["server_id"]
            ):
                raise ValueError
            return MCPSecretOperation(
                id=row["id"],
                server_id=row["server_id"],
                operation_kind=row["operation_kind"],
                new_refs=tuple(new_refs),
                old_refs=tuple(old_refs),
                state=row["state"],
            )
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise sqlite3.DatabaseError("invalid MCP secret operation") from error


@dataclass(slots=True)
class StagedSecretUpdate:
    operation: MCPSecretOperation
    _manager: MCPSecretManager = field(repr=False)
    _new_env_ref_items: tuple[tuple[str, MCPSecretRef], ...] = field(repr=False)
    _new_header_ref_items: tuple[tuple[str, MCPSecretRef], ...] = field(repr=False)
    _current_env_ref_items: tuple[tuple[str, MCPSecretRef], ...] = field(
        repr=False
    )
    _current_header_ref_items: tuple[tuple[str, MCPSecretRef], ...] = field(
        repr=False
    )
    _new_values: dict[str, str] = field(repr=False)
    _write_completed: bool = field(default=False, repr=False)

    @property
    def new_env_refs(self) -> Mapping[str, MCPSecretRef]:
        return MappingProxyType(dict(self._new_env_ref_items))

    @property
    def new_header_refs(self) -> Mapping[str, MCPSecretRef]:
        return MappingProxyType(dict(self._new_header_ref_items))

    def write_new_values(self) -> None:
        self._require_current_staged_operation()
        if self._write_completed:
            return
        try:
            for secret_ref, value in self._new_values.items():
                self._manager._store.put(secret_ref, value)
        except Exception:
            cleaned = self._manager._delete_refs(
                self.operation.server_id, self.operation.new_refs
            )
            self._new_values.clear()
            if cleaned:
                self._manager._journal.delete(self.operation.id)
            raise MCPDomainError("mcp_keychain_unavailable", retryable=True) from None
        self._new_values.clear()
        self._write_completed = True

    def commit_refs(
        self,
        connection: sqlite3.Connection,
        *,
        expected_revision: int,
        increment_config_revision: bool = True,
    ) -> None:
        if type(increment_config_revision) is not bool:
            raise MCPDomainError("mcp_revision_conflict")
        self._require_current_staged_operation(connection=connection)
        if (
            not increment_config_revision
            and self.operation.operation_kind != "upsert"
        ):
            raise MCPDomainError("mcp_revision_conflict")
        if self.operation.new_refs and not self._write_completed:
            raise MCPDomainError("mcp_keychain_unavailable", retryable=True)
        row = connection.execute(
            """SELECT config_revision, env_json, headers_json
               FROM mcp_servers WHERE id = ? AND config_revision = ?""",
            (self.operation.server_id, expected_revision),
        ).fetchone()
        if row is None:
            raise MCPDomainError("mcp_revision_conflict")

        _, persisted_env_refs = MCPRepository._settings_from_json(row["env_json"])
        _, persisted_header_refs = MCPRepository._settings_from_json(
            row["headers_json"]
        )
        if (
            persisted_env_refs != dict(self._current_env_ref_items)
            or persisted_header_refs != dict(self._current_header_ref_items)
        ):
            raise MCPDomainError("mcp_revision_conflict")

        if self.operation.operation_kind == "delete":
            cursor = connection.execute(
                "DELETE FROM mcp_servers WHERE id = ? AND config_revision = ?",
                (self.operation.server_id, expected_revision),
            )
        else:
            env, _ = MCPRepository._settings_from_json(row["env_json"])
            headers, _ = MCPRepository._settings_from_json(row["headers_json"])
            next_revision = (
                expected_revision + 1
                if increment_config_revision
                else expected_revision
            )
            cursor = connection.execute(
                """UPDATE mcp_servers
                   SET env_json = ?, headers_json = ?,
                       config_revision = ?,
                       secret_revision = secret_revision + 1,
                       updated_at = ?
                   WHERE id = ? AND config_revision = ?""",
                (
                    MCPRepository._settings_json(
                        env, dict(self._new_env_ref_items)
                    ),
                    MCPRepository._settings_json(
                        headers, dict(self._new_header_ref_items)
                    ),
                    next_revision,
                    _utc_now(),
                    self.operation.server_id,
                    expected_revision,
                ),
            )
        if cursor.rowcount != 1:
            raise MCPDomainError("mcp_revision_conflict")
        self._manager._journal.mark_state(
            self.operation.id,
            "committed",
            connection=connection,
            expected_state="staged",
        )

    def commit_created_refs(self, connection: sqlite3.Connection) -> None:
        """Commit refs already inserted with a newly-created server.

        This deliberately narrow path exists only because ``commit_refs``
        updates an existing server.  The repository inserts the server and
        calls this method in the same SQLite transaction.
        """
        self._require_current_staged_operation(connection=connection)
        if self.operation.operation_kind not in ("upsert", "migration"):
            raise MCPDomainError("mcp_revision_conflict")
        if self._current_env_ref_items or self._current_header_ref_items:
            raise MCPDomainError("mcp_revision_conflict")
        if self.operation.new_refs and not self._write_completed:
            raise MCPDomainError("mcp_keychain_unavailable", retryable=True)
        row = connection.execute(
            """SELECT config_revision, secret_revision, env_json, headers_json
               FROM mcp_servers WHERE id = ?""",
            (self.operation.server_id,),
        ).fetchone()
        if row is None or row["config_revision"] != 1:
            raise MCPDomainError("mcp_revision_conflict")
        _plain_env, persisted_env_refs = MCPRepository._settings_from_json(
            row["env_json"]
        )
        _plain_headers, persisted_header_refs = MCPRepository._settings_from_json(
            row["headers_json"]
        )
        final_env_refs = dict(self._new_env_ref_items)
        final_header_refs = dict(self._new_header_ref_items)
        expected_secret_revision = 1 if (final_env_refs or final_header_refs) else 0
        if (
            persisted_env_refs != final_env_refs
            or persisted_header_refs != final_header_refs
            or row["secret_revision"] != expected_secret_revision
        ):
            raise MCPDomainError("mcp_revision_conflict")
        self._manager._journal.mark_state(
            self.operation.id,
            "committed",
            connection=connection,
            expected_state="staged",
        )

    def rollback_new_refs(self) -> bool:
        operation = self._manager._journal.get(self.operation.id)
        if operation is None:
            return True
        if operation.state != "staged":
            return False
        if not self._manager._delete_refs(
            self.operation.server_id, self.operation.new_refs
        ):
            return False
        self._manager._journal.delete(operation.id)
        return True

    def cleanup_old_refs(self) -> bool:
        operation = self._manager._journal.get(self.operation.id)
        if operation is None:
            return True
        if operation.state not in ("committed", "cleanup_pending"):
            return False
        return self._manager._cleanup_old_refs(operation)

    def _require_current_staged_operation(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> MCPSecretOperation:
        current = self._manager._journal.get(
            self.operation.id, connection=connection
        )
        if (
            current is None
            or current.state != "staged"
            or current.id != self.operation.id
            or current.server_id != self.operation.server_id
            or current.operation_kind != self.operation.operation_kind
            or current.new_refs != self.operation.new_refs
            or current.old_refs != self.operation.old_refs
        ):
            raise MCPDomainError("mcp_revision_conflict")

        current_refs = {
            secret_ref.ref
            for _, secret_ref in (
                *self._current_env_ref_items,
                *self._current_header_ref_items,
            )
        }
        final_refs = {
            secret_ref.ref
            for _, secret_ref in (
                *self._new_env_ref_items,
                *self._new_header_ref_items,
            )
        }
        if set(current.new_refs) != final_refs - current_refs:
            raise MCPDomainError("mcp_revision_conflict")
        return current


class MCPSecretManager:
    def __init__(self, store: SecretStore, journal: MCPSecretJournal):
        self._store = store
        self._journal = journal

    def stage_patch(
        self,
        server_id: str,
        current_env_refs: Mapping[str, MCPSecretRef],
        current_header_refs: Mapping[str, MCPSecretRef],
        env_patch: SecretPatch,
        header_patch: SecretPatch,
        *,
        missing_env_keys: Sequence[str] = (),
        missing_header_keys: Sequence[str] = (),
        operation_kind: Literal["upsert", "migration"] = "upsert",
    ) -> StagedSecretUpdate:
        if operation_kind not in ("upsert", "migration"):
            raise MCPDomainError("mcp_config_invalid")
        _encoded_ref_segment(server_id)
        normalized_missing_env = self._validated_missing_keys(
            missing_env_keys,
            case_insensitive=False,
        )
        normalized_missing_headers = self._validated_missing_keys(
            missing_header_keys,
            case_insensitive=True,
        )
        self._reject_patch_missing_overlap(
            env_patch,
            normalized_missing_env,
            case_insensitive=False,
        )
        self._reject_patch_missing_overlap(
            header_patch,
            normalized_missing_headers,
            case_insensitive=True,
        )
        current_env = self._validated_refs(
            current_env_refs, server_id=server_id, namespace="env"
        )
        current_headers = self._validated_refs(
            current_header_refs, server_id=server_id, namespace="header"
        )
        next_env = dict(current_env)
        next_headers = dict(current_headers)
        old_refs: list[str] = []
        new_values: dict[str, str] = {}
        new_refs: list[str] = []

        self._apply_patch(
            server_id,
            "env",
            next_env,
            env_patch,
            old_refs,
            new_values,
            new_refs,
        )
        self._apply_patch(
            server_id,
            "header",
            next_headers,
            header_patch,
            old_refs,
            new_values,
            new_refs,
        )
        self._apply_missing_refs(
            server_id,
            "env",
            next_env,
            normalized_missing_env,
            old_refs,
            new_refs,
        )
        self._apply_missing_refs(
            server_id,
            "header",
            next_headers,
            normalized_missing_headers,
            old_refs,
            new_refs,
        )
        operation = self._journal._create(
            server_id=server_id,
            operation_kind=operation_kind,
            new_refs=_unique_refs(new_refs),
            old_refs=_unique_refs(old_refs),
        )
        return StagedSecretUpdate(
            operation=operation,
            _manager=self,
            _new_env_ref_items=tuple(next_env.items()),
            _new_header_ref_items=tuple(next_headers.items()),
            _current_env_ref_items=tuple(current_env.items()),
            _current_header_ref_items=tuple(current_headers.items()),
            _new_values=new_values,
        )

    def stage_delete(
        self,
        server_id: str,
        current_env_refs: Mapping[str, MCPSecretRef],
        current_header_refs: Mapping[str, MCPSecretRef],
    ) -> StagedSecretUpdate:
        _encoded_ref_segment(server_id)
        current_env = self._validated_refs(
            current_env_refs, server_id=server_id, namespace="env"
        )
        current_headers = self._validated_refs(
            current_header_refs, server_id=server_id, namespace="header"
        )
        operation = self._journal._create(
            server_id=server_id,
            operation_kind="delete",
            new_refs=(),
            old_refs=_unique_refs(
                [ref.ref for ref in (*current_env.values(), *current_headers.values())]
            ),
        )
        return StagedSecretUpdate(
            operation=operation,
            _manager=self,
            _new_env_ref_items=(),
            _new_header_ref_items=(),
            _current_env_ref_items=tuple(current_env.items()),
            _current_header_ref_items=tuple(current_headers.items()),
            _new_values={},
        )

    def resolve(
        self,
        server_id: str,
        namespace: Literal["env", "header"],
        refs: Mapping[str, MCPSecretRef],
    ) -> dict[str, str]:
        if namespace not in ("env", "header"):
            raise MCPDomainError("mcp_config_invalid")
        _encoded_ref_segment(server_id)
        resolved: dict[str, str] = {}
        validated = self._validated_refs(
            refs, server_id=server_id, namespace=namespace
        )
        for field_name, secret_ref in validated.items():
            try:
                value = self._store.get(secret_ref.ref)
            except Exception:
                raise MCPDomainError(
                    "mcp_keychain_unavailable", retryable=True
                ) from None
            if value is None:
                raise MCPDomainError("mcp_credentials_missing")
            resolved[field_name] = value
        return resolved

    def matches_secret_values(
        self,
        server_id: str,
        namespace: Literal["env", "header"],
        refs: Mapping[str, MCPSecretRef],
        expected: Mapping[str, str | None],
    ) -> bool:
        """Compare a legacy source with storage without returning secret data."""
        if namespace not in ("env", "header"):
            raise MCPDomainError("mcp_config_invalid")
        validated = self._validated_refs(
            refs,
            server_id=server_id,
            namespace=namespace,
        )
        if set(validated) != set(expected):
            return False
        for field_name, expected_value in expected.items():
            if expected_value is not None and not isinstance(expected_value, str):
                raise TypeError("expected MCP secret values must be strings or null")
            try:
                actual = self._store.get(validated[field_name].ref)
            except Exception:
                raise MCPDomainError(
                    "mcp_keychain_unavailable", retryable=True
                ) from None
            if expected_value is None:
                if actual is not None:
                    return False
                continue
            if actual is None or not hmac.compare_digest(
                actual.encode("utf-8", "strict"),
                expected_value.encode("utf-8", "strict"),
            ):
                return False
        return True

    def reconcile_pending_operations(self) -> SecretCleanupReport:
        completed = 0
        operations, invalid_count = self._journal._list_pending_isolated()
        pending = invalid_count
        for operation in operations:
            if operation.state == "staged":
                if operation.operation_kind == "delete":
                    if self._server_exists(operation.server_id):
                        self._journal.delete(operation.id)
                        completed += 1
                        continue
                    self._journal.mark_state(
                        operation.id,
                        "committed",
                        expected_state="staged",
                    )
                    operation = self._journal.get(operation.id)
                elif operation.new_refs and self._server_references(
                    operation.server_id, operation.new_refs
                ):
                    self._journal.mark_state(
                        operation.id,
                        "committed",
                        expected_state="staged",
                    )
                    operation = self._journal.get(operation.id)
                else:
                    if self._delete_refs(operation.server_id, operation.new_refs):
                        self._journal.delete(operation.id)
                        completed += 1
                    else:
                        pending += 1
                    continue

            if operation is not None and self._cleanup_old_refs(operation):
                completed += 1
            else:
                pending += 1
        return SecretCleanupReport(
            completed_operations=completed,
            pending_operations=pending,
        )

    @staticmethod
    def _validated_missing_keys(
        field_names: Sequence[str],
        *,
        case_insensitive: bool,
    ) -> tuple[str, ...]:
        if isinstance(field_names, (str, bytes)):
            raise TypeError("missing MCP secret keys must be a sequence")
        result: list[str] = []
        seen: set[str] = set()
        for field_name in field_names:
            if not isinstance(field_name, str) or not field_name:
                raise TypeError("missing MCP secret keys must be unique strings")
            comparable = field_name.casefold() if case_insensitive else field_name
            if comparable in seen:
                raise TypeError("missing MCP secret keys must be unique strings")
            seen.add(comparable)
            result.append(field_name)
        return tuple(result)

    @staticmethod
    def _reject_patch_missing_overlap(
        patch: SecretPatch,
        missing_keys: Sequence[str],
        *,
        case_insensitive: bool,
    ) -> None:
        if patch is None:
            return
        if not isinstance(patch, Mapping):
            raise TypeError("MCP secret patch must be a mapping")
        patch_keys: set[str] = set()
        for field_name in patch:
            if not isinstance(field_name, str) or not field_name:
                raise TypeError("MCP secret patch keys must be non-empty strings")
            patch_keys.add(field_name.casefold() if case_insensitive else field_name)
        comparable_missing = {
            key.casefold() if case_insensitive else key for key in missing_keys
        }
        if patch_keys & comparable_missing:
            raise MCPDomainError("mcp_config_invalid")

    @staticmethod
    def _validated_refs(
        refs: Mapping[str, MCPSecretRef],
        *,
        server_id: str | None = None,
        namespace: Literal["env", "header"] | None = None,
    ) -> dict[str, MCPSecretRef]:
        result: dict[str, MCPSecretRef] = {}
        for field_name, secret_ref in refs.items():
            if (
                not isinstance(field_name, str)
                or not field_name
                or not isinstance(secret_ref, MCPSecretRef)
            ):
                raise TypeError("MCP secret references must use named MCPSecretRef values")
            if server_id is not None and namespace is not None:
                prefix = _secret_ref_prefix(server_id, namespace, field_name)
                version = secret_ref.ref.removeprefix(prefix)
                if (
                    not secret_ref.ref.startswith(prefix)
                    or not version
                    or ":" in version
                ):
                    raise MCPDomainError("mcp_config_invalid")
            result[field_name] = secret_ref
        return result

    @staticmethod
    def _apply_patch(
        server_id: str,
        namespace: Literal["env", "header"],
        refs: dict[str, MCPSecretRef],
        patch: SecretPatch,
        old_refs: list[str],
        new_values: dict[str, str],
        new_refs: list[str],
    ) -> None:
        if patch is None:
            return
        if not isinstance(patch, Mapping):
            raise TypeError("MCP secret patch must be a mapping")
        for field_name, value in patch.items():
            if not isinstance(field_name, str) or not field_name:
                raise TypeError("MCP secret patch keys must be non-empty strings")
            previous = refs.get(field_name)
            if previous is not None:
                old_refs.append(previous.ref)
            if value is None:
                refs.pop(field_name, None)
                continue
            if not isinstance(value, str):
                raise TypeError("MCP secret patch values must be strings or null")
            ref = f"{_secret_ref_prefix(server_id, namespace, field_name)}{uuid4()}"
            refs[field_name] = MCPSecretRef(ref)
            new_values[ref] = value
            new_refs.append(ref)

    @staticmethod
    def _apply_missing_refs(
        server_id: str,
        namespace: Literal["env", "header"],
        refs: dict[str, MCPSecretRef],
        field_names: Sequence[str],
        old_refs: list[str],
        new_refs: list[str],
    ) -> None:
        if isinstance(field_names, (str, bytes)):
            raise TypeError("missing MCP secret keys must be a sequence")
        seen: set[str] = set()
        for field_name in field_names:
            if not isinstance(field_name, str) or not field_name or field_name in seen:
                raise TypeError("missing MCP secret keys must be unique strings")
            seen.add(field_name)
            previous = refs.get(field_name)
            if previous is not None:
                old_refs.append(previous.ref)
            ref = f"{_secret_ref_prefix(server_id, namespace, field_name)}{uuid4()}"
            refs[field_name] = MCPSecretRef(ref)
            new_refs.append(ref)

    def _delete_refs(self, server_id: str, refs: tuple[str, ...]) -> bool:
        if not all(self._ref_belongs_to_server(server_id, ref) for ref in refs):
            return False
        if not refs:
            return True
        active_refs = self._active_secret_refs()
        if active_refs is None or set(refs) & active_refs:
            return False
        succeeded = True
        for secret_ref in refs:
            try:
                self._store.delete(secret_ref)
            except Exception:
                succeeded = False
        return succeeded

    @staticmethod
    def _ref_belongs_to_server(server_id: str, secret_ref: str) -> bool:
        try:
            prefix = f"mcp:{_encoded_ref_segment(server_id)}:"
        except MCPDomainError:
            return False
        if not isinstance(secret_ref, str) or not secret_ref.startswith(prefix):
            return False
        scoped = secret_ref[len(prefix) :]
        if scoped.startswith("env:"):
            field_and_version = scoped[len("env:") :]
        elif scoped.startswith("header:"):
            field_and_version = scoped[len("header:") :]
        else:
            return False
        field_name, separator, version = field_and_version.rpartition(":")
        return bool(
            separator
            and field_name
            and ":" not in field_name
            and version
            and ":" not in version
        )

    def _active_secret_refs(self) -> set[str] | None:
        try:
            with self._journal.database.connect() as connection:
                rows = connection.execute(
                    "SELECT env_json, headers_json FROM mcp_servers"
                ).fetchall()
            active: set[str] = set()
            for row in rows:
                _, env_refs = MCPRepository._settings_from_json(row["env_json"])
                _, header_refs = MCPRepository._settings_from_json(
                    row["headers_json"]
                )
                active.update(
                    ref.ref for ref in (*env_refs.values(), *header_refs.values())
                )
            return active
        except (sqlite3.Error, TypeError, ValueError):
            return None

    def _cleanup_old_refs(self, operation: MCPSecretOperation) -> bool:
        if self._delete_refs(operation.server_id, operation.old_refs):
            self._journal.delete(operation.id)
            return True
        self._journal.mark_state(operation.id, "cleanup_pending")
        return False

    def _server_exists(self, server_id: str) -> bool:
        with self._journal.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM mcp_servers WHERE id = ?",
                (server_id,),
            ).fetchone()
        return row is not None

    def _server_references(self, server_id: str, refs: tuple[str, ...]) -> bool:
        with self._journal.database.connect() as connection:
            row = connection.execute(
                "SELECT env_json, headers_json FROM mcp_servers WHERE id = ?",
                (server_id,),
            ).fetchone()
        if row is None:
            return False
        _, env_refs = MCPRepository._settings_from_json(row["env_json"])
        _, header_refs = MCPRepository._settings_from_json(row["headers_json"])
        persisted = {ref.ref for ref in (*env_refs.values(), *header_refs.values())}
        return set(refs).issubset(persisted)
