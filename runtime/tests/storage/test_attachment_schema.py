import sqlite3
from dataclasses import FrozenInstanceError

import pytest

from verilex_runtime.storage import migrations as migration_module
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import MIGRATIONS, run_migrations


SHA_A = "a" * 64


def _insert_provider_thread_turn(connection) -> None:
    connection.execute(
        "INSERT INTO providers ("
        "id, name, kind, base_url, default_model, auth_mode, secret_ref, enabled, "
        "created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "provider-1",
            "Provider",
            "openai_compatible",
            "http://127.0.0.1:11434/v1",
            "model-1",
            "none",
            None,
            1,
            "2026-08-14T00:00:00Z",
            "2026-08-14T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO threads ("
        "id, title, provider_id, model, workspace_id, status, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "thread-1",
            "Thread",
            "provider-1",
            "model-1",
            None,
            "idle",
            "2026-08-14T00:00:00Z",
            "2026-08-14T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO turns (id, thread_id, status, created_at) VALUES (?, ?, ?, ?)",
        (
            "turn-1",
            "thread-1",
            "completed",
            "2026-08-14T00:00:00Z",
        ),
    )


def _insert_blob(
    connection,
    *,
    sha256: str = SHA_A,
    media_type: str = "text/plain",
    size_bytes: int = 12,
    extracted_text_path: str | None = "extracted/a.txt",
    extraction_status: str = "ready",
    extraction_error_code: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO attachment_blobs ("
        "sha256, media_type, size_bytes, stored_path, extracted_text_path, "
        "extraction_status, extraction_error_code, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sha256,
            media_type,
            size_bytes,
            f"blobs/{sha256[:2]}/{sha256}",
            extracted_text_path,
            extraction_status,
            extraction_error_code,
            "2026-08-14T00:00:00Z",
        ),
    )


def _insert_attachment(
    connection,
    *,
    attachment_id: str = "attachment-1",
    display_name: str = "evidence.txt",
    media_type: str = "text/plain",
    size_bytes: int = 12,
) -> None:
    connection.execute(
        "INSERT INTO attachments ("
        "id, thread_id, turn_id, sha256, display_name, media_type, size_bytes, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            attachment_id,
            "thread-1",
            "turn-1",
            SHA_A,
            display_name,
            media_type,
            size_bytes,
            "2026-08-14T00:00:00Z",
        ),
    )


def _insert_upload(
    connection,
    *,
    upload_id: str,
    attachment_id: str | None = None,
    display_name: str = "evidence.txt",
) -> None:
    connection.execute(
        "INSERT INTO attachment_uploads ("
        "id, thread_id, sha256, display_name, created_at, expires_at, attachment_id"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            upload_id,
            "thread-1",
            SHA_A,
            display_name,
            "2026-08-14T00:00:00Z",
            "2026-08-15T00:00:00Z",
            attachment_id,
        ),
    )


def _insert_model_capability(
    connection,
    *,
    model: str,
    supports_vision: int = 0,
    supports_native_pdf: int = 0,
    source: str = "user_declared",
) -> None:
    connection.execute(
        "INSERT INTO model_capabilities ("
        "provider_id, model, supports_vision, supports_native_pdf, source, "
        "created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "provider-1",
            model,
            supports_vision,
            supports_native_pdf,
            source,
            "2026-08-14T00:00:00Z",
            "2026-08-14T00:00:00Z",
        ),
    )


def test_migration_v6_creates_attachment_schema(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")

    run_migrations(database)
    run_migrations(database)

    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        upload_fks = {
            (row[3], row[2], row[4], row[6])
            for row in connection.execute(
                "PRAGMA foreign_key_list(attachment_uploads)"
            )
        }
        attachment_fks = {
            (row[3], row[2], row[4], row[6])
            for row in connection.execute("PRAGMA foreign_key_list(attachments)")
        }
        capability_fks = {
            (row[3], row[2], row[4], row[6])
            for row in connection.execute(
                "PRAGMA foreign_key_list(model_capabilities)"
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

    assert {
        "model_capabilities",
        "attachment_blobs",
        "attachment_uploads",
        "attachments",
    } <= tables
    assert ("thread_id", "threads", "id", "CASCADE") in upload_fks
    assert ("sha256", "attachment_blobs", "sha256", "RESTRICT") in upload_fks
    assert ("attachment_id", "attachments", "id", "SET NULL") in upload_fks
    assert ("thread_id", "threads", "id", "CASCADE") in attachment_fks
    assert ("turn_id", "turns", "id", "CASCADE") in attachment_fks
    assert ("sha256", "attachment_blobs", "sha256", "RESTRICT") in attachment_fks
    assert ("provider_id", "providers", "id", "CASCADE") in capability_fks
    assert {
        "attachment_uploads_thread_id_idx",
        "attachment_uploads_sha256_idx",
        "attachment_uploads_expires_at_idx",
        "attachments_thread_id_idx",
        "attachments_turn_id_idx",
        "attachments_sha256_idx",
    } <= indexes
    assert foreign_key_violations == []


@pytest.mark.parametrize(
    "media_type",
    [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
    ],
)
def test_attachment_schema_accepts_standard_docx_and_dotx_media_types(
    tmp_path, media_type
):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        _insert_provider_thread_turn(connection)
        _insert_blob(
            connection,
            media_type=media_type,
            extracted_text_path=None,
            extraction_status="not_required",
        )
        _insert_attachment(
            connection,
            display_name="contract.docx",
            media_type=media_type,
        )
        assert connection.execute(
            "SELECT media_type FROM attachments WHERE id='attachment-1'"
        ).fetchone()[0] == media_type


def test_migration_v6_upgrades_v5_without_losing_existing_rows(
    tmp_path, monkeypatch
):
    database = Database(tmp_path / "verilex.sqlite3")
    all_migrations = migration_module.MIGRATIONS
    with monkeypatch.context() as scoped:
        scoped.setattr(migration_module, "MIGRATIONS", all_migrations[:5])
        run_migrations(database)

    with database.connect() as connection:
        _insert_provider_thread_turn(connection)
        connection.execute(
            "INSERT INTO messages ("
            "id, thread_id, turn_id, sequence, role, content_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "message-1",
                "thread-1",
                "turn-1",
                1,
                "user",
                '"hello"',
                "2026-08-14T00:00:00Z",
            ),
        )

    run_migrations(database)

    with database.connect() as connection:
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        message = connection.execute(
            "SELECT content_json FROM messages WHERE id = 'message-1'"
        ).fetchone()
        foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

    assert versions == [migration[0] for migration in MIGRATIONS]
    assert tuple(message) == ('"hello"',)
    assert foreign_key_violations == []


@pytest.mark.parametrize("sha256", ["a" * 63, "A" * 64, "g" * 64])
def test_attachment_blob_rejects_noncanonical_sha256(tmp_path, sha256):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)

    with database.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_blob(connection, sha256=sha256)


@pytest.mark.parametrize(
    "overrides",
    [
        {"size_bytes": -1},
        {"media_type": "application/zip"},
        {"extraction_status": "pending"},
        {"extraction_status": "ready", "extracted_text_path": None},
        {
            "extraction_status": "not_required",
            "extracted_text_path": "extracted/a.txt",
        },
        {
            "extraction_status": "failed",
            "extracted_text_path": None,
            "extraction_error_code": None,
        },
    ],
)
def test_attachment_blob_rejects_invalid_metadata(tmp_path, overrides):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)

    with database.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_blob(connection, **overrides)


def test_model_capability_accepts_declared_sources_and_rejects_legacy_user(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)

    with database.connect() as connection:
        _insert_provider_thread_turn(connection)
        _insert_model_capability(
            connection,
            model="declared-model",
            supports_vision=1,
        )
        _insert_model_capability(
            connection,
            model="curated-model",
            supports_vision=1,
            source="curated",
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_model_capability(
                connection,
                model="legacy-model",
                supports_vision=1,
                source="user",
            )


def test_model_capability_provider_and_model_pair_is_unique(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)

    with database.connect() as connection:
        _insert_provider_thread_turn(connection)
        _insert_model_capability(
            connection,
            model="model-1",
            supports_vision=1,
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_model_capability(connection, model="model-1")


def test_model_capability_accepts_native_pdf_boolean_true(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)

    with database.connect() as connection:
        _insert_provider_thread_turn(connection)
        _insert_model_capability(
            connection,
            model="native-pdf-model",
            supports_native_pdf=1,
        )
        stored_value = connection.execute(
            "SELECT supports_native_pdf FROM model_capabilities "
            "WHERE provider_id = ? AND model = ?",
            ("provider-1", "native-pdf-model"),
        ).fetchone()[0]

    assert stored_value == 1


@pytest.mark.parametrize(
    ("model", "supports_vision", "supports_native_pdf", "source"),
    [
        ("", 0, 0, "user_declared"),
        ("m" * 201, 0, 0, "user_declared"),
        ("invalid-vision", 2, 0, "user_declared"),
        ("invalid-native-pdf-negative", 0, -1, "user_declared"),
        ("invalid-native-pdf-above-one", 0, 2, "user_declared"),
        ("invalid-source", 0, 0, "adapter"),
    ],
)
def test_model_capability_rejects_each_invalid_field_independently(
    tmp_path,
    model,
    supports_vision,
    supports_native_pdf,
    source,
):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)

    with database.connect() as connection:
        _insert_provider_thread_turn(connection)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_model_capability(
                connection,
                model=model,
                supports_vision=supports_vision,
                supports_native_pdf=supports_native_pdf,
                source=source,
            )


@pytest.mark.parametrize("display_name", ["", "法" * 85 + "a"])
@pytest.mark.parametrize("record_kind", ["upload", "attachment"])
def test_upload_and_attachment_reject_display_names_outside_utf8_byte_limit(
    tmp_path,
    display_name,
    record_kind,
):
    assert len(display_name.encode("utf-8")) in (0, 256)
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)

    with database.connect() as connection:
        _insert_provider_thread_turn(connection)
        _insert_blob(connection)
        with pytest.raises(sqlite3.IntegrityError):
            if record_kind == "upload":
                _insert_upload(
                    connection,
                    upload_id="upload-invalid-name",
                    display_name=display_name,
                )
            else:
                _insert_attachment(connection, display_name=display_name)


@pytest.mark.parametrize(
    ("media_type", "size_bytes"),
    [("application/zip", 12), ("text/plain", -1)],
)
def test_attachment_rejects_invalid_media_type_or_negative_size(
    tmp_path,
    media_type,
    size_bytes,
):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)

    with database.connect() as connection:
        _insert_provider_thread_turn(connection)
        _insert_blob(connection)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_attachment(
                connection,
                media_type=media_type,
                size_bytes=size_bytes,
            )


def test_upload_attachment_id_is_unique_and_delete_attachment_sets_null(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)

    with database.connect() as connection:
        _insert_provider_thread_turn(connection)
        _insert_blob(connection)
        _insert_attachment(connection)
        _insert_upload(connection, upload_id="upload-1", attachment_id="attachment-1")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_upload(
                connection,
                upload_id="upload-2",
                attachment_id="attachment-1",
            )

        connection.execute("DELETE FROM attachments WHERE id = 'attachment-1'")
        attachment_id = connection.execute(
            "SELECT attachment_id FROM attachment_uploads WHERE id = 'upload-1'"
        ).fetchone()[0]

    assert attachment_id is None


def test_thread_and_provider_deletes_cascade_but_referenced_blob_is_restricted(
    tmp_path,
):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)

    with database.connect() as connection:
        _insert_provider_thread_turn(connection)
        _insert_blob(connection)
        _insert_attachment(connection)
        _insert_upload(connection, upload_id="upload-1", attachment_id="attachment-1")
        _insert_model_capability(
            connection,
            model="model-1",
            supports_vision=1,
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM attachment_blobs WHERE sha256 = ?", (SHA_A,)
            )

        connection.execute("UPDATE threads SET provider_id = NULL WHERE id = 'thread-1'")
        connection.execute("DELETE FROM providers WHERE id = 'provider-1'")
        capability_count = connection.execute(
            "SELECT COUNT(*) FROM model_capabilities"
        ).fetchone()[0]
        connection.execute("DELETE FROM threads WHERE id = 'thread-1'")
        upload_count = connection.execute(
            "SELECT COUNT(*) FROM attachment_uploads"
        ).fetchone()[0]
        attachment_count = connection.execute(
            "SELECT COUNT(*) FROM attachments"
        ).fetchone()[0]
        blob_count = connection.execute(
            "SELECT COUNT(*) FROM attachment_blobs"
        ).fetchone()[0]

    assert capability_count == 0
    assert upload_count == 0
    assert attachment_count == 0
    assert blob_count == 1


def test_attachment_storage_records_are_immutable_and_use_persisted_status_values():
    from verilex_runtime.storage.models import (
        AttachmentBlobRecord,
        AttachmentRecord,
        AttachmentUploadRecord,
        ExtractionStatus,
        ModelCapabilityRecord,
    )

    records = (
        ModelCapabilityRecord(
            provider_id="provider-1",
            model="model-1",
            supports_vision=True,
            supports_native_pdf=False,
            source="user_declared",
            created_at="2026-08-14T00:00:00Z",
            updated_at="2026-08-14T00:00:00Z",
        ),
        AttachmentBlobRecord(
            sha256=SHA_A,
            media_type="text/plain",
            size_bytes=12,
            stored_path=f"blobs/aa/{SHA_A}",
            extracted_text_path=f"extracted/{SHA_A}.txt",
            extraction_status=ExtractionStatus.READY,
            extraction_error_code=None,
            created_at="2026-08-14T00:00:00Z",
        ),
        AttachmentUploadRecord(
            id="upload-1",
            thread_id="thread-1",
            sha256=SHA_A,
            display_name="evidence.txt",
            created_at="2026-08-14T00:00:00Z",
            expires_at="2026-08-15T00:00:00Z",
        ),
        AttachmentRecord(
            id="attachment-1",
            thread_id="thread-1",
            turn_id="turn-1",
            sha256=SHA_A,
            display_name="evidence.txt",
            media_type="text/plain",
            size_bytes=12,
            created_at="2026-08-14T00:00:00Z",
        ),
    )

    assert [status.value for status in ExtractionStatus] == [
        "not_required",
        "ready",
        "failed",
    ]
    for record in records:
        with pytest.raises(FrozenInstanceError):
            record.created_at = "2026-08-15T00:00:00Z"
