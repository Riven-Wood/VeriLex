import sqlite3
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from verilex_runtime.storage.database import Database
from verilex_runtime.knowledge.schema import KNOWLEDGE_SCHEMA


MIGRATIONS = (
    (
        1,
        "create providers",
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );

        CREATE TABLE providers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('openai_compatible', 'ollama')),
            base_url TEXT NOT NULL,
            default_model TEXT NOT NULL,
            auth_mode TEXT NOT NULL CHECK (auth_mode IN ('api_key', 'none')),
            secret_ref TEXT UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (
                (auth_mode = 'api_key' AND secret_ref IS NOT NULL) OR
                (auth_mode = 'none' AND secret_ref IS NULL)
            ),
            CHECK (kind != 'ollama' OR auth_mode = 'none')
        );

        CREATE INDEX providers_enabled_idx ON providers(enabled);
        """,
    ),
    (
        2,
        "create threads turns messages events",
        """
        CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            provider_id TEXT REFERENCES providers(id) ON DELETE RESTRICT,
            model TEXT NOT NULL,
            workspace_id TEXT,
            status TEXT NOT NULL CHECK (
                status IN ('idle', 'running', 'awaiting_approval', 'completed', 'failed', 'cancelled')
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT,
            deleted_at TEXT
        );

        CREATE INDEX threads_status_idx ON threads(status);
        CREATE INDEX threads_deleted_at_idx ON threads(deleted_at);
        CREATE INDEX threads_archived_at_idx ON threads(archived_at);

        CREATE TABLE turns (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (
                status IN ('queued', 'running', 'awaiting_approval', 'completed', 'failed', 'cancelled', 'interrupted')
            ),
            error_code TEXT,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX turns_thread_id_idx ON turns(thread_id);

        CREATE TABLE messages (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            turn_id TEXT REFERENCES turns(id) ON DELETE SET NULL,
            sequence INTEGER NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
            content_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (thread_id, sequence)
        );

        CREATE INDEX messages_thread_id_idx ON messages(thread_id);

        CREATE TABLE events (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (turn_id, sequence)
        );

        CREATE INDEX events_turn_id_idx ON events(turn_id);

        CREATE UNIQUE INDEX turns_one_active_per_thread
            ON turns(thread_id)
            WHERE status IN ('queued', 'running', 'awaiting_approval');
        """,
    ),
    (
        3,
        "create workspaces tool_calls approvals",
        """
        CREATE TABLE workspaces (
            id TEXT PRIMARY KEY,
            root_path TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            allow_read INTEGER NOT NULL DEFAULT 1 CHECK (allow_read IN (0, 1)),
            allow_write INTEGER NOT NULL DEFAULT 0 CHECK (allow_write IN (0, 1)),
            created_at TEXT NOT NULL,
            last_used_at TEXT NOT NULL
        );

        CREATE TABLE tool_calls (
            id TEXT PRIMARY KEY,
            turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
            provider_call_id TEXT,
            name TEXT NOT NULL,
            arguments_json TEXT NOT NULL,
            risk_level TEXT NOT NULL CHECK (
                risk_level IN ('low', 'medium', 'high')
            ),
            status TEXT NOT NULL CHECK (
                status IN (
                    'proposed', 'awaiting_approval', 'authorized', 'running',
                    'succeeded', 'failed', 'denied', 'cancelled'
                )
            ),
            result_json TEXT,
            started_at TEXT,
            finished_at TEXT,
            UNIQUE (turn_id, provider_call_id)
        );

        CREATE INDEX tool_calls_turn_id_idx ON tool_calls(turn_id);

        CREATE TABLE approvals (
            id TEXT PRIMARY KEY,
            tool_call_id TEXT NOT NULL UNIQUE REFERENCES tool_calls(id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'allowed', 'denied', 'cancelled', 'expired')
            ),
            decision_reason TEXT,
            requested_at TEXT NOT NULL,
            resolved_at TEXT
        );
        """,
    ),
    (
        4,
        "rebuild threads with workspace foreign key",
        """
        CREATE TABLE threads_new (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            provider_id TEXT REFERENCES providers(id) ON DELETE RESTRICT,
            model TEXT NOT NULL,
            workspace_id TEXT REFERENCES workspaces(id) ON DELETE SET NULL,
            status TEXT NOT NULL CHECK (
                status IN ('idle', 'running', 'awaiting_approval', 'completed', 'failed', 'cancelled')
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT,
            deleted_at TEXT
        );

        INSERT INTO threads_new (
            id, title, provider_id, model, workspace_id, status,
            created_at, updated_at, archived_at, deleted_at
        )
        SELECT
            id, title, provider_id, model, workspace_id, status,
            created_at, updated_at, archived_at, deleted_at
        FROM threads;

        DROP TABLE threads;

        ALTER TABLE threads_new RENAME TO threads;

        CREATE INDEX threads_status_idx ON threads(status);
        CREATE INDEX threads_deleted_at_idx ON threads(deleted_at);
        CREATE INDEX threads_archived_at_idx ON threads(archived_at);
        """,
    ),
    (
        5,
        "create audit log",
        """
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT,
            turn_id TEXT,
            tool_call_id TEXT,
            action TEXT NOT NULL,
            subject TEXT NOT NULL,
            outcome TEXT NOT NULL,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX audit_log_thread_id_idx ON audit_log(thread_id);
        CREATE INDEX audit_log_turn_id_idx ON audit_log(turn_id);
        CREATE INDEX audit_log_tool_call_id_idx ON audit_log(tool_call_id);
        CREATE INDEX audit_log_created_at_idx ON audit_log(created_at);
        """,
    ),
    (
        6,
        "create model capabilities and attachments",
        """
        CREATE TABLE model_capabilities (
            provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
            model TEXT NOT NULL CHECK (length(model) BETWEEN 1 AND 200),
            supports_vision INTEGER NOT NULL DEFAULT 0 CHECK (supports_vision IN (0, 1)),
            supports_native_pdf INTEGER NOT NULL DEFAULT 0 CHECK (supports_native_pdf IN (0, 1)),
            source TEXT NOT NULL CHECK (source IN ('curated', 'user_declared')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (provider_id, model)
        );

        CREATE TABLE attachment_blobs (
            sha256 TEXT PRIMARY KEY CHECK (
                length(sha256) = 64 AND sha256 = lower(sha256) AND
                sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            media_type TEXT NOT NULL CHECK (
                media_type IN ('text/plain', 'application/pdf', 'image/png', 'image/jpeg', 'image/webp')
            ),
            size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
            stored_path TEXT NOT NULL,
            extracted_text_path TEXT,
            extraction_status TEXT NOT NULL CHECK (
                extraction_status IN ('not_required', 'ready', 'failed')
            ),
            extraction_error_code TEXT,
            created_at TEXT NOT NULL,
            CHECK (
                (extraction_status = 'ready' AND extracted_text_path IS NOT NULL AND extraction_error_code IS NULL) OR
                (extraction_status = 'not_required' AND extracted_text_path IS NULL AND extraction_error_code IS NULL) OR
                (extraction_status = 'failed' AND extracted_text_path IS NULL AND extraction_error_code IS NOT NULL)
            )
        );

        CREATE TABLE attachment_uploads (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            sha256 TEXT NOT NULL REFERENCES attachment_blobs(sha256) ON DELETE RESTRICT,
            display_name TEXT NOT NULL CHECK (
                length(CAST(display_name AS BLOB)) BETWEEN 1 AND 255
            ),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT,
            attachment_id TEXT UNIQUE REFERENCES attachments(id) ON DELETE SET NULL
        );

        CREATE INDEX attachment_uploads_thread_id_idx ON attachment_uploads(thread_id);
        CREATE INDEX attachment_uploads_sha256_idx ON attachment_uploads(sha256);
        CREATE INDEX attachment_uploads_expires_at_idx ON attachment_uploads(expires_at);

        CREATE TABLE attachments (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
            sha256 TEXT NOT NULL REFERENCES attachment_blobs(sha256) ON DELETE RESTRICT,
            display_name TEXT NOT NULL CHECK (
                length(CAST(display_name AS BLOB)) BETWEEN 1 AND 255
            ),
            media_type TEXT NOT NULL CHECK (
                media_type IN ('text/plain', 'application/pdf', 'image/png', 'image/jpeg', 'image/webp')
            ),
            size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
            created_at TEXT NOT NULL
        );

        CREATE INDEX attachments_thread_id_idx ON attachments(thread_id);
        CREATE INDEX attachments_turn_id_idx ON attachments(turn_id);
        CREATE INDEX attachments_sha256_idx ON attachments(sha256);
        """,
    ),
    (
        7,
        "add hidden draft threads",
        """
        ALTER TABLE threads
            ADD COLUMN is_draft INTEGER NOT NULL DEFAULT 0
            CHECK (is_draft IN (0, 1));

        CREATE INDEX threads_is_draft_idx ON threads(is_draft);
        """,
    ),
    (
        8,
        "add reasoning and context usage metadata",
        """
        ALTER TABLE threads
            ADD COLUMN reasoning_effort TEXT NOT NULL DEFAULT 'auto'
            CHECK (reasoning_effort IN ('auto', 'low', 'medium', 'high'));

        ALTER TABLE model_capabilities
            ADD COLUMN context_window_tokens INTEGER
            CHECK (context_window_tokens BETWEEN 1 AND 10000000);

        ALTER TABLE model_capabilities
            ADD COLUMN supported_reasoning_efforts_json TEXT;

        ALTER TABLE model_capabilities
            ADD COLUMN context_window_source TEXT NOT NULL DEFAULT 'unknown'
            CHECK (context_window_source IN ('unknown', 'curated', 'user_declared', 'provider'));

        ALTER TABLE model_capabilities
            ADD COLUMN reasoning_efforts_source TEXT NOT NULL DEFAULT 'unknown'
            CHECK (reasoning_efforts_source IN ('unknown', 'curated', 'user_declared', 'provider'));

        CREATE TABLE thread_context_usage (
            thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE,
            provider_id TEXT NOT NULL,
            model TEXT NOT NULL CHECK (length(model) BETWEEN 1 AND 200),
            input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
            cached_input_tokens INTEGER NOT NULL DEFAULT 0 CHECK (cached_input_tokens >= 0),
            output_tokens INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
            reasoning_output_tokens INTEGER NOT NULL DEFAULT 0 CHECK (reasoning_output_tokens >= 0),
            source TEXT NOT NULL CHECK (source IN ('provider')),
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        9,
        "add model context window overrides",
        """
        CREATE TABLE model_context_windows (
            provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
            model TEXT NOT NULL CHECK (length(model) BETWEEN 1 AND 200),
            automatic_tokens INTEGER
                CHECK (automatic_tokens BETWEEN 1 AND 10000000),
            automatic_source TEXT NOT NULL DEFAULT 'unknown'
                CHECK (automatic_source IN ('unknown', 'provider', 'curated')),
            manual_tokens INTEGER
                CHECK (manual_tokens BETWEEN 1 AND 10000000),
            is_available INTEGER NOT NULL DEFAULT 1
                CHECK (is_available IN (0, 1)),
            created_at TEXT NOT NULL,
            refreshed_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (provider_id, model),
            CHECK (
                (automatic_tokens IS NULL AND automatic_source = 'unknown') OR
                (automatic_tokens IS NOT NULL AND automatic_source IN ('provider', 'curated'))
            )
        );

        CREATE INDEX model_context_windows_provider_available_idx
            ON model_context_windows(provider_id, is_available);

        INSERT INTO model_context_windows (
            provider_id, model, automatic_tokens, automatic_source,
            manual_tokens, is_available, created_at, refreshed_at, updated_at
        )
        SELECT
            provider_id,
            model,
            CASE
                WHEN context_window_source IN ('provider', 'curated')
                    THEN context_window_tokens
                ELSE NULL
            END,
            CASE
                WHEN context_window_source IN ('provider', 'curated')
                    THEN context_window_source
                ELSE 'unknown'
            END,
            CASE
                WHEN context_window_source = 'user_declared'
                    THEN context_window_tokens
                ELSE NULL
            END,
            1,
            created_at,
            updated_at,
            updated_at
        FROM model_capabilities
        WHERE context_window_tokens IS NOT NULL
          AND context_window_source IN ('provider', 'curated', 'user_declared');
        """,
    ),
    (
        10,
        "create background tasks and runtime preferences",
        """
        CREATE TABLE background_tasks (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
            tool_call_id TEXT NOT NULL REFERENCES tool_calls(id) ON DELETE CASCADE,
            command TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('running', 'completed', 'failed', 'stopped', 'expired', 'interrupted')
            ),
            exit_code INTEGER,
            output_text TEXT,
            omitted_bytes INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX idx_background_tasks_thread ON background_tasks(thread_id);

        CREATE TABLE runtime_preferences (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        11,
        "create thread web access",
        """
        CREATE TABLE thread_web_access (
            thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE,
            enabled_at TEXT NOT NULL,
            granted_at TEXT,
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        12,
        "create thread scoped mcp configuration",
        """
        CREATE TABLE mcp_servers (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            transport TEXT NOT NULL CHECK (transport IN ('stdio', 'http', 'sse')),
            command TEXT,
            args_json TEXT NOT NULL DEFAULT '[]',
            cwd TEXT,
            url TEXT,
            env_json TEXT NOT NULL DEFAULT '{}',
            headers_json TEXT NOT NULL DEFAULT '{}',
            connect_timeout_ms INTEGER NOT NULL CHECK (
                connect_timeout_ms BETWEEN 1000 AND 120000
            ),
            request_timeout_ms INTEGER NOT NULL CHECK (
                request_timeout_ms BETWEEN 1000 AND 600000
            ),
            config_revision INTEGER NOT NULL DEFAULT 1,
            config_fingerprint TEXT NOT NULL,
            secret_revision INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (
                (transport = 'stdio' AND command IS NOT NULL AND url IS NULL) OR
                (transport IN ('http', 'sse') AND url IS NOT NULL AND command IS NULL)
            )
        );

        CREATE TABLE thread_mcp_servers (
            thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            server_id TEXT NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
            selected_at TEXT NOT NULL,
            PRIMARY KEY (thread_id, server_id)
        );

        CREATE TABLE mcp_server_tools (
            server_id TEXT NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
            tool_name TEXT NOT NULL,
            description TEXT NOT NULL,
            description_hash TEXT NOT NULL,
            schema_json TEXT NOT NULL,
            schema_hash TEXT NOT NULL,
            enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
            review_state TEXT NOT NULL CHECK (
                review_state IN ('known', 'new', 'changed')
            ),
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (server_id, tool_name)
        );
        """,
    ),
    (
        13,
        "journal immutable mcp secrets",
        """
        CREATE TABLE mcp_secret_operations (
            id TEXT PRIMARY KEY,
            server_id TEXT,
            operation_kind TEXT NOT NULL CHECK (
                operation_kind IN ('upsert', 'delete', 'migration')
            ),
            new_refs_json TEXT NOT NULL,
            old_refs_json TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN ('staged', 'committed', 'cleanup_pending')
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX mcp_secret_operations_state_idx
            ON mcp_secret_operations(state, created_at);

        ALTER TABLE mcp_servers ADD COLUMN config_review_state TEXT NOT NULL
            DEFAULT 'reviewed' CHECK (
                config_review_state IN ('reviewed', 'needs_review', 'blocked')
            );
        ALTER TABLE mcp_servers ADD COLUMN risk_findings_json TEXT NOT NULL
            DEFAULT '[]';
        """,
    ),
    (
        14,
        "skills disabled state",
        """
        CREATE TABLE disabled_skills (
            skill_name TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        15,
        "add document artifacts and docx attachments",
        """
        ALTER TABLE workspaces ADD COLUMN revoked_at TEXT;
        CREATE INDEX workspaces_revoked_at_idx ON workspaces(revoked_at);

        CREATE TABLE attachment_blobs_v15 (
            sha256 TEXT PRIMARY KEY CHECK (
                length(sha256) = 64 AND sha256 = lower(sha256) AND
                sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            media_type TEXT NOT NULL CHECK (
                media_type IN (
                    'text/plain', 'application/pdf', 'image/png', 'image/jpeg',
                    'image/webp',
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.template'
                )
            ),
            size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
            stored_path TEXT NOT NULL,
            extracted_text_path TEXT,
            extraction_status TEXT NOT NULL CHECK (
                extraction_status IN ('not_required', 'ready', 'failed')
            ),
            extraction_error_code TEXT,
            created_at TEXT NOT NULL,
            CHECK (
                (extraction_status = 'ready' AND extracted_text_path IS NOT NULL AND extraction_error_code IS NULL) OR
                (extraction_status = 'not_required' AND extracted_text_path IS NULL AND extraction_error_code IS NULL) OR
                (extraction_status = 'failed' AND extracted_text_path IS NULL AND extraction_error_code IS NOT NULL)
            )
        );

        INSERT INTO attachment_blobs_v15
        SELECT * FROM attachment_blobs;

        CREATE TABLE attachments_v15 (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
            sha256 TEXT NOT NULL REFERENCES attachment_blobs_v15(sha256) ON DELETE RESTRICT,
            display_name TEXT NOT NULL CHECK (
                length(CAST(display_name AS BLOB)) BETWEEN 1 AND 255
            ),
            media_type TEXT NOT NULL CHECK (
                media_type IN (
                    'text/plain', 'application/pdf', 'image/png', 'image/jpeg',
                    'image/webp',
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.template'
                )
            ),
            size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
            created_at TEXT NOT NULL
        );

        INSERT INTO attachments_v15
        SELECT * FROM attachments;

        DROP TABLE attachments;
        DROP TABLE attachment_blobs;
        ALTER TABLE attachment_blobs_v15 RENAME TO attachment_blobs;
        ALTER TABLE attachments_v15 RENAME TO attachments;

        CREATE INDEX attachments_thread_id_idx ON attachments(thread_id);
        CREATE INDEX attachments_turn_id_idx ON attachments(turn_id);
        CREATE INDEX attachments_sha256_idx ON attachments(sha256);

        CREATE TABLE artifact_blobs (
            sha256 TEXT PRIMARY KEY CHECK (
                length(sha256) = 64 AND sha256 = lower(sha256) AND
                sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            size_bytes INTEGER NOT NULL CHECK (
                size_bytes BETWEEN 0 AND 104857600
            ),
            stored_path TEXT NOT NULL CHECK (
                length(CAST(stored_path AS BLOB)) BETWEEN 1 AND 1024 AND
                stored_path NOT LIKE '/%' AND stored_path != '..' AND
                stored_path NOT LIKE '../%' AND stored_path NOT LIKE '%/../%' AND
                stored_path NOT LIKE '%/..' AND stored_path NOT LIKE './%' AND
                stored_path NOT LIKE '%/./%' AND stored_path NOT LIKE '%/.' AND
                stored_path NOT LIKE '%//%' AND stored_path NOT GLOB '[A-Za-z]:*' AND
                stored_path != '.' AND stored_path NOT LIKE '%/' AND
                instr(stored_path, ':') = 0 AND
                ('/' || stored_path || '/') NOT GLOB '*./*' AND
                ('/' || stored_path || '/') NOT GLOB '* /*' AND
                ('/' || upper(stored_path) || '/') NOT GLOB '*/CON/*' AND
                ('/' || upper(stored_path) || '/') NOT GLOB '*/CON.*/*' AND
                ('/' || upper(stored_path) || '/') NOT GLOB '*/PRN/*' AND
                ('/' || upper(stored_path) || '/') NOT GLOB '*/PRN.*/*' AND
                ('/' || upper(stored_path) || '/') NOT GLOB '*/AUX/*' AND
                ('/' || upper(stored_path) || '/') NOT GLOB '*/AUX.*/*' AND
                ('/' || upper(stored_path) || '/') NOT GLOB '*/NUL/*' AND
                ('/' || upper(stored_path) || '/') NOT GLOB '*/NUL.*/*' AND
                ('/' || upper(stored_path) || '/') NOT GLOB '*/COM[1-9]/*' AND
                ('/' || upper(stored_path) || '/') NOT GLOB '*/COM[1-9].*/*' AND
                ('/' || upper(stored_path) || '/') NOT GLOB '*/LPT[1-9]/*' AND
                ('/' || upper(stored_path) || '/') NOT GLOB '*/LPT[1-9].*/*' AND
                instr(stored_path, char(0)) = 0 AND instr(stored_path, '\\') = 0
            ),
            created_at TEXT NOT NULL
        );

        CREATE TABLE artifacts (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
            thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
            tool_call_id TEXT NOT NULL REFERENCES tool_calls(id) ON DELETE CASCADE,
            blob_sha256 TEXT NOT NULL REFERENCES artifact_blobs(sha256) ON DELETE RESTRICT,
            display_name TEXT NOT NULL CHECK (
                length(CAST(display_name AS BLOB)) BETWEEN 1 AND 255 AND
                instr(display_name, '/') = 0 AND instr(display_name, '\\') = 0 AND
                instr(display_name, char(0)) = 0
            ),
            media_type TEXT NOT NULL CHECK (
                length(CAST(media_type AS BLOB)) BETWEEN 1 AND 255
            ),
            size_bytes INTEGER NOT NULL CHECK (
                size_bytes BETWEEN 0 AND 104857600
            ),
            operation TEXT NOT NULL CHECK (
                length(CAST(operation AS BLOB)) BETWEEN 1 AND 128
            ),
            source_kind TEXT NOT NULL CHECK (
                source_kind IN ('new', 'workspace', 'attachment', 'artifact')
            ),
            parent_artifact_id TEXT REFERENCES artifacts(id) ON DELETE CASCADE,
            source_attachment_id TEXT REFERENCES attachments(id) ON DELETE RESTRICT,
            source_workspace_id TEXT REFERENCES workspaces(id) ON DELETE RESTRICT,
            source_relative_path TEXT,
            published_workspace_id TEXT REFERENCES workspaces(id) ON DELETE RESTRICT,
            published_relative_path TEXT,
            structure_status TEXT NOT NULL CHECK (
                structure_status IN ('passed', 'not_required')
            ),
            render_status TEXT NOT NULL CHECK (
                render_status IN ('passed', 'not_required')
            ),
            vision_status TEXT NOT NULL CHECK (
                vision_status IN ('passed', 'partial', 'unsupported', 'failed', 'not_run')
            ),
            validation_report_json TEXT NOT NULL CHECK (
                length(CAST(validation_report_json AS BLOB)) <= 131072 AND
                json_valid(validation_report_json) AND
                json_type(validation_report_json, '$.schema_version') = 'integer' AND
                json_extract(validation_report_json, '$.schema_version') = 1 AND
                json_remove(
                    validation_report_json, '$.schema_version', '$.structure',
                    '$.render', '$.vision'
                ) = '{}' AND
                (json_type(validation_report_json, '$.structure.status') IS NULL OR (
                    json_type(validation_report_json, '$.structure.status') = 'text' AND
                    json_extract(validation_report_json, '$.structure.status') = structure_status
                )) AND
                (json_type(validation_report_json, '$.render.status') IS NULL OR (
                    json_type(validation_report_json, '$.render.status') = 'text' AND
                    json_extract(validation_report_json, '$.render.status') = render_status
                )) AND
                (json_type(validation_report_json, '$.vision.status') IS NULL OR (
                    json_type(validation_report_json, '$.vision.status') = 'text' AND
                    json_extract(validation_report_json, '$.vision.status') = vision_status
                ))
            ),
            created_at TEXT NOT NULL,
            CHECK (
                (source_relative_path IS NULL OR (
                    length(CAST(source_relative_path AS BLOB)) BETWEEN 1 AND 1024 AND
                    source_relative_path NOT LIKE '/%' AND source_relative_path != '..' AND
                    source_relative_path NOT LIKE '../%' AND source_relative_path NOT LIKE '%/../%' AND
                    source_relative_path NOT LIKE '%/..' AND source_relative_path NOT LIKE './%' AND
                    source_relative_path NOT LIKE '%/./%' AND source_relative_path NOT LIKE '%/.' AND
                    source_relative_path NOT LIKE '%//%' AND source_relative_path NOT GLOB '[A-Za-z]:*' AND
                    source_relative_path != '.' AND source_relative_path NOT LIKE '%/' AND
                    instr(source_relative_path, ':') = 0 AND
                    ('/' || source_relative_path || '/') NOT GLOB '*./*' AND
                    ('/' || source_relative_path || '/') NOT GLOB '* /*' AND
                    ('/' || upper(source_relative_path) || '/') NOT GLOB '*/CON/*' AND
                    ('/' || upper(source_relative_path) || '/') NOT GLOB '*/CON.*/*' AND
                    ('/' || upper(source_relative_path) || '/') NOT GLOB '*/PRN/*' AND
                    ('/' || upper(source_relative_path) || '/') NOT GLOB '*/PRN.*/*' AND
                    ('/' || upper(source_relative_path) || '/') NOT GLOB '*/AUX/*' AND
                    ('/' || upper(source_relative_path) || '/') NOT GLOB '*/AUX.*/*' AND
                    ('/' || upper(source_relative_path) || '/') NOT GLOB '*/NUL/*' AND
                    ('/' || upper(source_relative_path) || '/') NOT GLOB '*/NUL.*/*' AND
                    ('/' || upper(source_relative_path) || '/') NOT GLOB '*/COM[1-9]/*' AND
                    ('/' || upper(source_relative_path) || '/') NOT GLOB '*/COM[1-9].*/*' AND
                    ('/' || upper(source_relative_path) || '/') NOT GLOB '*/LPT[1-9]/*' AND
                    ('/' || upper(source_relative_path) || '/') NOT GLOB '*/LPT[1-9].*/*' AND
                    instr(source_relative_path, char(0)) = 0 AND instr(source_relative_path, '\\') = 0
                ))
            ),
            CHECK (
                (published_relative_path IS NULL OR (
                    length(CAST(published_relative_path AS BLOB)) BETWEEN 1 AND 1024 AND
                    published_relative_path NOT LIKE '/%' AND published_relative_path != '..' AND
                    published_relative_path NOT LIKE '../%' AND published_relative_path NOT LIKE '%/../%' AND
                    published_relative_path NOT LIKE '%/..' AND published_relative_path NOT LIKE './%' AND
                    published_relative_path NOT LIKE '%/./%' AND published_relative_path NOT LIKE '%/.' AND
                    published_relative_path NOT LIKE '%//%' AND published_relative_path NOT GLOB '[A-Za-z]:*' AND
                    published_relative_path != '.' AND published_relative_path NOT LIKE '%/' AND
                    instr(published_relative_path, ':') = 0 AND
                    ('/' || published_relative_path || '/') NOT GLOB '*./*' AND
                    ('/' || published_relative_path || '/') NOT GLOB '* /*' AND
                    ('/' || upper(published_relative_path) || '/') NOT GLOB '*/CON/*' AND
                    ('/' || upper(published_relative_path) || '/') NOT GLOB '*/CON.*/*' AND
                    ('/' || upper(published_relative_path) || '/') NOT GLOB '*/PRN/*' AND
                    ('/' || upper(published_relative_path) || '/') NOT GLOB '*/PRN.*/*' AND
                    ('/' || upper(published_relative_path) || '/') NOT GLOB '*/AUX/*' AND
                    ('/' || upper(published_relative_path) || '/') NOT GLOB '*/AUX.*/*' AND
                    ('/' || upper(published_relative_path) || '/') NOT GLOB '*/NUL/*' AND
                    ('/' || upper(published_relative_path) || '/') NOT GLOB '*/NUL.*/*' AND
                    ('/' || upper(published_relative_path) || '/') NOT GLOB '*/COM[1-9]/*' AND
                    ('/' || upper(published_relative_path) || '/') NOT GLOB '*/COM[1-9].*/*' AND
                    ('/' || upper(published_relative_path) || '/') NOT GLOB '*/LPT[1-9]/*' AND
                    ('/' || upper(published_relative_path) || '/') NOT GLOB '*/LPT[1-9].*/*' AND
                    instr(published_relative_path, char(0)) = 0 AND instr(published_relative_path, '\\') = 0
                ))
            ),
            CHECK (
                (published_workspace_id IS NULL AND published_relative_path IS NULL) OR
                (published_workspace_id IS NOT NULL AND published_relative_path IS NOT NULL)
            ),
            CHECK (
                (source_kind = 'new' AND parent_artifact_id IS NULL AND source_attachment_id IS NULL AND source_workspace_id IS NULL AND source_relative_path IS NULL) OR
                (source_kind = 'workspace' AND parent_artifact_id IS NULL AND source_attachment_id IS NULL AND source_workspace_id IS NOT NULL AND source_relative_path IS NOT NULL) OR
                (source_kind = 'attachment' AND parent_artifact_id IS NULL AND source_attachment_id IS NOT NULL AND source_workspace_id IS NULL AND source_relative_path IS NULL) OR
                (source_kind = 'artifact' AND parent_artifact_id IS NOT NULL AND source_attachment_id IS NULL AND source_workspace_id IS NULL AND source_relative_path IS NULL)
            )
        );

        CREATE INDEX artifacts_thread_created_idx
            ON artifacts(thread_id, created_at DESC, id DESC);
        CREATE INDEX artifacts_turn_idx ON artifacts(turn_id);
        CREATE INDEX artifacts_tool_call_idx ON artifacts(tool_call_id);
        CREATE INDEX artifacts_blob_idx ON artifacts(blob_sha256);
        CREATE INDEX artifacts_parent_idx ON artifacts(parent_artifact_id);

        CREATE TRIGGER artifacts_chain_insert
        BEFORE INSERT ON artifacts
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM tool_calls tc
                JOIN turns t ON t.id = tc.turn_id
                WHERE tc.id = NEW.tool_call_id AND tc.turn_id = NEW.turn_id
                  AND t.thread_id = NEW.thread_id
            ) THEN RAISE(ABORT, 'artifact tool chain mismatch') END;
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM artifact_blobs b
                WHERE b.sha256 = NEW.blob_sha256 AND b.size_bytes = NEW.size_bytes
            ) THEN RAISE(ABORT, 'artifact blob mismatch') END;
            SELECT CASE WHEN NEW.parent_artifact_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM artifacts a
                WHERE a.id = NEW.parent_artifact_id AND a.thread_id = NEW.thread_id
            ) THEN RAISE(ABORT, 'artifact parent mismatch') END;
            SELECT CASE WHEN NEW.source_attachment_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM attachments a
                WHERE a.id = NEW.source_attachment_id AND a.thread_id = NEW.thread_id
            ) THEN RAISE(ABORT, 'artifact attachment mismatch') END;
            SELECT CASE WHEN NEW.source_workspace_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM threads t
                WHERE t.id = NEW.thread_id AND t.workspace_id = NEW.source_workspace_id
            ) THEN RAISE(ABORT, 'artifact source workspace mismatch') END;
            SELECT CASE WHEN NEW.published_workspace_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM threads t
                WHERE t.id = NEW.thread_id AND t.workspace_id = NEW.published_workspace_id
            ) THEN RAISE(ABORT, 'artifact published workspace mismatch') END;
        END;

        CREATE TRIGGER artifacts_chain_update
        BEFORE UPDATE ON artifacts
        BEGIN
            SELECT CASE WHEN
                NEW.id != OLD.id OR NEW.thread_id != OLD.thread_id OR
                NEW.turn_id != OLD.turn_id OR NEW.tool_call_id != OLD.tool_call_id OR
                NEW.blob_sha256 != OLD.blob_sha256 OR NEW.display_name != OLD.display_name OR
                NEW.media_type != OLD.media_type OR NEW.size_bytes != OLD.size_bytes OR
                NEW.operation != OLD.operation OR NEW.source_kind != OLD.source_kind OR
                NEW.parent_artifact_id IS NOT OLD.parent_artifact_id OR
                NEW.source_attachment_id IS NOT OLD.source_attachment_id OR
                NEW.source_workspace_id IS NOT OLD.source_workspace_id OR
                NEW.source_relative_path IS NOT OLD.source_relative_path OR
                NEW.published_workspace_id IS NOT OLD.published_workspace_id OR
                NEW.published_relative_path IS NOT OLD.published_relative_path OR
                NEW.structure_status != OLD.structure_status OR
                NEW.render_status != OLD.render_status OR NEW.created_at != OLD.created_at
            THEN RAISE(ABORT, 'artifact immutable fields changed') END;
            SELECT CASE WHEN (
                NEW.vision_status != OLD.vision_status OR
                NEW.validation_report_json != OLD.validation_report_json
            ) AND NOT (
                OLD.vision_status = 'not_run' AND NEW.vision_status IN (
                    'passed', 'partial', 'unsupported', 'failed'
                )
            ) THEN RAISE(ABORT, 'artifact vision transition invalid') END;
        END;

        CREATE TABLE artifact_publish_journal (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
            batch_id TEXT NOT NULL CHECK (length(batch_id) BETWEEN 1 AND 128),
            ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
            batch_count INTEGER NOT NULL CHECK (
                batch_count BETWEEN 1 AND 10 AND ordinal < batch_count
            ),
            artifact_id TEXT NOT NULL UNIQUE CHECK (length(artifact_id) BETWEEN 1 AND 128),
            thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
            tool_call_id TEXT NOT NULL REFERENCES tool_calls(id) ON DELETE CASCADE,
            blob_sha256 TEXT NOT NULL CHECK (
                length(blob_sha256) = 64 AND blob_sha256 = lower(blob_sha256) AND
                blob_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            blob_temp_relative_path TEXT NOT NULL CHECK (
                length(CAST(blob_temp_relative_path AS BLOB)) BETWEEN 1 AND 1024 AND
                blob_temp_relative_path NOT LIKE '/%' AND blob_temp_relative_path != '..' AND
                blob_temp_relative_path NOT LIKE '../%' AND blob_temp_relative_path NOT LIKE '%/../%' AND
                blob_temp_relative_path NOT LIKE '%/..' AND blob_temp_relative_path NOT LIKE './%' AND
                blob_temp_relative_path NOT LIKE '%/./%' AND blob_temp_relative_path NOT LIKE '%/.' AND
                blob_temp_relative_path NOT LIKE '%//%' AND blob_temp_relative_path NOT GLOB '[A-Za-z]:*' AND
                blob_temp_relative_path != '.' AND blob_temp_relative_path NOT LIKE '%/' AND
                instr(blob_temp_relative_path, ':') = 0 AND
                ('/' || blob_temp_relative_path || '/') NOT GLOB '*./*' AND
                ('/' || blob_temp_relative_path || '/') NOT GLOB '* /*' AND
                ('/' || upper(blob_temp_relative_path) || '/') NOT GLOB '*/CON/*' AND
                ('/' || upper(blob_temp_relative_path) || '/') NOT GLOB '*/CON.*/*' AND
                ('/' || upper(blob_temp_relative_path) || '/') NOT GLOB '*/PRN/*' AND
                ('/' || upper(blob_temp_relative_path) || '/') NOT GLOB '*/PRN.*/*' AND
                ('/' || upper(blob_temp_relative_path) || '/') NOT GLOB '*/AUX/*' AND
                ('/' || upper(blob_temp_relative_path) || '/') NOT GLOB '*/AUX.*/*' AND
                ('/' || upper(blob_temp_relative_path) || '/') NOT GLOB '*/NUL/*' AND
                ('/' || upper(blob_temp_relative_path) || '/') NOT GLOB '*/NUL.*/*' AND
                ('/' || upper(blob_temp_relative_path) || '/') NOT GLOB '*/COM[1-9]/*' AND
                ('/' || upper(blob_temp_relative_path) || '/') NOT GLOB '*/COM[1-9].*/*' AND
                ('/' || upper(blob_temp_relative_path) || '/') NOT GLOB '*/LPT[1-9]/*' AND
                ('/' || upper(blob_temp_relative_path) || '/') NOT GLOB '*/LPT[1-9].*/*' AND
                instr(blob_temp_relative_path, char(0)) = 0 AND instr(blob_temp_relative_path, '\\') = 0
            ),
            published_workspace_id TEXT REFERENCES workspaces(id) ON DELETE RESTRICT,
            workspace_temp_relative_path TEXT,
            published_relative_path TEXT,
            state TEXT NOT NULL CHECK (
                state IN ('prepared', 'blob_ready', 'workspace_ready')
            ),
            record_json TEXT NOT NULL CHECK (
                length(CAST(record_json AS BLOB)) <= 131072 AND
                json_valid(record_json) AND
                json_type(record_json, '$.schema_version') = 'integer' AND
                json_extract(record_json, '$.schema_version') = 1 AND
                json_type(record_json, '$.artifact') = 'object' AND
                json_remove(record_json, '$.schema_version', '$.artifact') = '{}'
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(batch_id, ordinal),
            CHECK (
                (published_workspace_id IS NULL AND workspace_temp_relative_path IS NULL AND published_relative_path IS NULL) OR
                (published_workspace_id IS NOT NULL AND workspace_temp_relative_path IS NOT NULL AND published_relative_path IS NOT NULL)
            ),
            CHECK (workspace_temp_relative_path IS NULL OR (
                length(CAST(workspace_temp_relative_path AS BLOB)) BETWEEN 1 AND 1024 AND
                workspace_temp_relative_path NOT LIKE '/%' AND workspace_temp_relative_path != '..' AND
                workspace_temp_relative_path NOT LIKE '../%' AND workspace_temp_relative_path NOT LIKE '%/../%' AND
                workspace_temp_relative_path NOT LIKE '%/..' AND workspace_temp_relative_path NOT LIKE './%' AND
                workspace_temp_relative_path NOT LIKE '%/./%' AND workspace_temp_relative_path NOT LIKE '%/.' AND
                workspace_temp_relative_path NOT LIKE '%//%' AND workspace_temp_relative_path NOT GLOB '[A-Za-z]:*' AND
                workspace_temp_relative_path != '.' AND workspace_temp_relative_path NOT LIKE '%/' AND
                instr(workspace_temp_relative_path, ':') = 0 AND
                ('/' || workspace_temp_relative_path || '/') NOT GLOB '*./*' AND
                ('/' || workspace_temp_relative_path || '/') NOT GLOB '* /*' AND
                ('/' || upper(workspace_temp_relative_path) || '/') NOT GLOB '*/CON/*' AND
                ('/' || upper(workspace_temp_relative_path) || '/') NOT GLOB '*/CON.*/*' AND
                ('/' || upper(workspace_temp_relative_path) || '/') NOT GLOB '*/PRN/*' AND
                ('/' || upper(workspace_temp_relative_path) || '/') NOT GLOB '*/PRN.*/*' AND
                ('/' || upper(workspace_temp_relative_path) || '/') NOT GLOB '*/AUX/*' AND
                ('/' || upper(workspace_temp_relative_path) || '/') NOT GLOB '*/AUX.*/*' AND
                ('/' || upper(workspace_temp_relative_path) || '/') NOT GLOB '*/NUL/*' AND
                ('/' || upper(workspace_temp_relative_path) || '/') NOT GLOB '*/NUL.*/*' AND
                ('/' || upper(workspace_temp_relative_path) || '/') NOT GLOB '*/COM[1-9]/*' AND
                ('/' || upper(workspace_temp_relative_path) || '/') NOT GLOB '*/COM[1-9].*/*' AND
                ('/' || upper(workspace_temp_relative_path) || '/') NOT GLOB '*/LPT[1-9]/*' AND
                ('/' || upper(workspace_temp_relative_path) || '/') NOT GLOB '*/LPT[1-9].*/*' AND
                instr(workspace_temp_relative_path, char(0)) = 0 AND instr(workspace_temp_relative_path, '\\') = 0
            )),
            CHECK (published_relative_path IS NULL OR (
                length(CAST(published_relative_path AS BLOB)) BETWEEN 1 AND 1024 AND
                published_relative_path NOT LIKE '/%' AND published_relative_path != '..' AND
                published_relative_path NOT LIKE '../%' AND published_relative_path NOT LIKE '%/../%' AND
                published_relative_path NOT LIKE '%/..' AND published_relative_path NOT LIKE './%' AND
                published_relative_path NOT LIKE '%/./%' AND published_relative_path NOT LIKE '%/.' AND
                published_relative_path NOT LIKE '%//%' AND published_relative_path NOT GLOB '[A-Za-z]:*' AND
                published_relative_path != '.' AND published_relative_path NOT LIKE '%/' AND
                instr(published_relative_path, ':') = 0 AND
                ('/' || published_relative_path || '/') NOT GLOB '*./*' AND
                ('/' || published_relative_path || '/') NOT GLOB '* /*' AND
                ('/' || upper(published_relative_path) || '/') NOT GLOB '*/CON/*' AND
                ('/' || upper(published_relative_path) || '/') NOT GLOB '*/CON.*/*' AND
                ('/' || upper(published_relative_path) || '/') NOT GLOB '*/PRN/*' AND
                ('/' || upper(published_relative_path) || '/') NOT GLOB '*/PRN.*/*' AND
                ('/' || upper(published_relative_path) || '/') NOT GLOB '*/AUX/*' AND
                ('/' || upper(published_relative_path) || '/') NOT GLOB '*/AUX.*/*' AND
                ('/' || upper(published_relative_path) || '/') NOT GLOB '*/NUL/*' AND
                ('/' || upper(published_relative_path) || '/') NOT GLOB '*/NUL.*/*' AND
                ('/' || upper(published_relative_path) || '/') NOT GLOB '*/COM[1-9]/*' AND
                ('/' || upper(published_relative_path) || '/') NOT GLOB '*/COM[1-9].*/*' AND
                ('/' || upper(published_relative_path) || '/') NOT GLOB '*/LPT[1-9]/*' AND
                ('/' || upper(published_relative_path) || '/') NOT GLOB '*/LPT[1-9].*/*' AND
                instr(published_relative_path, char(0)) = 0 AND instr(published_relative_path, '\\') = 0
            ))
        );

        CREATE INDEX artifact_publish_journal_batch_idx
            ON artifact_publish_journal(batch_id, ordinal);
        CREATE INDEX artifact_publish_journal_state_idx
            ON artifact_publish_journal(state, updated_at);
        CREATE INDEX artifact_publish_journal_tool_call_idx
            ON artifact_publish_journal(tool_call_id);

        CREATE TRIGGER artifact_publish_journal_chain_insert
        BEFORE INSERT ON artifact_publish_journal
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM tool_calls tc
                JOIN turns t ON t.id = tc.turn_id
                WHERE tc.id = NEW.tool_call_id AND tc.turn_id = NEW.turn_id
                  AND t.thread_id = NEW.thread_id
            ) THEN RAISE(ABORT, 'artifact journal tool chain mismatch') END;
            SELECT CASE WHEN NEW.published_workspace_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM threads t
                WHERE t.id = NEW.thread_id AND t.workspace_id = NEW.published_workspace_id
            ) THEN RAISE(ABORT, 'artifact journal workspace mismatch') END;
            SELECT CASE WHEN EXISTS (
                SELECT 1 FROM artifact_publish_journal j
                WHERE j.batch_id = NEW.batch_id AND (
                    j.batch_count != NEW.batch_count OR
                    j.thread_id != NEW.thread_id OR j.turn_id != NEW.turn_id OR
                    j.tool_call_id != NEW.tool_call_id
                )
            ) THEN RAISE(ABORT, 'artifact journal batch mismatch') END;
            SELECT CASE WHEN EXISTS (
                SELECT 1 FROM artifact_publish_journal j
                WHERE j.tool_call_id = NEW.tool_call_id AND j.batch_id != NEW.batch_id
            ) THEN RAISE(ABORT, 'artifact journal tool batch mismatch') END;
            SELECT CASE WHEN EXISTS (
                SELECT 1 FROM artifacts a
                WHERE a.tool_call_id = NEW.tool_call_id
            ) THEN RAISE(ABORT, 'artifact tool call already committed') END;
        END;

        CREATE TRIGGER artifact_publish_journal_update
        BEFORE UPDATE ON artifact_publish_journal
        BEGIN
            SELECT CASE WHEN
                NEW.id != OLD.id OR NEW.batch_id != OLD.batch_id OR
                NEW.ordinal != OLD.ordinal OR NEW.batch_count != OLD.batch_count OR
                NEW.artifact_id != OLD.artifact_id OR
                NEW.thread_id != OLD.thread_id OR NEW.turn_id != OLD.turn_id OR
                NEW.tool_call_id != OLD.tool_call_id OR
                NEW.blob_sha256 != OLD.blob_sha256 OR
                NEW.blob_temp_relative_path != OLD.blob_temp_relative_path OR
                NEW.published_workspace_id IS NOT OLD.published_workspace_id OR
                NEW.workspace_temp_relative_path IS NOT OLD.workspace_temp_relative_path OR
                NEW.published_relative_path IS NOT OLD.published_relative_path OR
                NEW.record_json != OLD.record_json OR NEW.created_at != OLD.created_at
            THEN RAISE(ABORT, 'artifact journal immutable fields changed') END;
            SELECT CASE WHEN NOT (
                (OLD.state = 'prepared' AND NEW.state IN ('prepared', 'blob_ready')) OR
                (OLD.state = 'blob_ready' AND NEW.state IN ('blob_ready', 'workspace_ready')) OR
                (OLD.state = 'workspace_ready' AND NEW.state = 'workspace_ready')
            ) THEN RAISE(ABORT, 'artifact journal state transition invalid') END;
        END;

        CREATE TABLE model_capability_observations (
            provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
            model TEXT NOT NULL CHECK (length(model) BETWEEN 1 AND 200),
            capability TEXT NOT NULL CHECK (capability IN ('vision')),
            supported INTEGER NOT NULL CHECK (supported IN (0, 1)),
            provider_updated_at TEXT NOT NULL CHECK (
                length(provider_updated_at) BETWEEN 1 AND 64
            ),
            evidence_code TEXT NOT NULL CHECK (
                length(evidence_code) BETWEEN 1 AND 100 AND
                evidence_code NOT GLOB '*[^a-z0-9_]*'
            ),
            observed_at TEXT NOT NULL CHECK (length(observed_at) BETWEEN 1 AND 64),
            PRIMARY KEY (provider_id, model, capability)
        );

        CREATE INDEX model_capability_observations_provider_idx
            ON model_capability_observations(provider_id, model);
        """,
    ),
    (
        16,
        "bind artifact workspace file identity",
        """
        ALTER TABLE artifact_publish_journal
            ADD COLUMN workspace_file_device INTEGER
            CHECK (workspace_file_device IS NULL OR workspace_file_device >= 0);
        ALTER TABLE artifact_publish_journal
            ADD COLUMN workspace_file_inode INTEGER
            CHECK (workspace_file_inode IS NULL OR workspace_file_inode >= 0);

        CREATE TRIGGER artifact_publish_journal_identity_insert
        BEFORE INSERT ON artifact_publish_journal
        BEGIN
            SELECT CASE WHEN
                NEW.workspace_file_device IS NOT NULL OR
                NEW.workspace_file_inode IS NOT NULL
            THEN RAISE(ABORT, 'artifact journal identity must be bound later') END;
        END;

        CREATE TRIGGER artifact_publish_journal_identity_update
        BEFORE UPDATE OF workspace_file_device, workspace_file_inode
        ON artifact_publish_journal
        BEGIN
            SELECT CASE WHEN NOT (
                NEW.workspace_file_device IS OLD.workspace_file_device AND
                NEW.workspace_file_inode IS OLD.workspace_file_inode
            ) AND NOT (
                OLD.workspace_file_device IS NULL AND
                OLD.workspace_file_inode IS NULL AND
                NEW.workspace_file_device IS NOT NULL AND
                NEW.workspace_file_inode IS NOT NULL AND
                NEW.published_workspace_id IS NOT NULL AND
                OLD.state = 'blob_ready' AND NEW.state = 'blob_ready'
            ) THEN RAISE(ABORT, 'artifact journal identity transition invalid') END;
        END;

        CREATE TRIGGER artifact_publish_journal_workspace_ready_identity
        BEFORE UPDATE OF state ON artifact_publish_journal
        WHEN NEW.state = 'workspace_ready'
        BEGIN
            SELECT CASE WHEN
                NEW.workspace_file_device IS NULL OR
                NEW.workspace_file_inode IS NULL
            THEN RAISE(ABORT, 'artifact journal workspace identity missing') END;
        END;
        """,
    ),
    (
        17,
        "create law cache",
        """
        CREATE TABLE law_cache (
            bbbs TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            flxz TEXT NOT NULL DEFAULT '',
            zdjg_name TEXT NOT NULL DEFAULT '',
            sxx INTEGER NOT NULL,
            gbrq TEXT NOT NULL DEFAULT '',
            sxrq TEXT NOT NULL DEFAULT '',
            body_text TEXT NOT NULL,
            source_url TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        );
        """,
    ),
    (
        18,
        "add thread scene",
        """
        ALTER TABLE threads
            ADD COLUMN scene TEXT NOT NULL DEFAULT 'general'
            CHECK (scene IN ('general', 'legal-retrieval'));
        """,
    ),
    # 2026-09-05: interim builds briefly shipped v19 as the law search
    # history table; the feature moved onto scene threads and the table was
    # dropped, but machines that ran those builds have v19 applied. The
    # migration stays as an idempotent no-op with the same name so their
    # schema history keeps validating; the table itself is unused.
    (
        19,
        "create law search history",
        """
        CREATE TABLE IF NOT EXISTS law_search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL,
            result_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """,
    ),
    (
        20,
        "persist legal retrieval choices",
        """
        ALTER TABLE threads ADD COLUMN legal_channel TEXT
            CHECK (legal_channel IN ('web', 'plugin'));
        ALTER TABLE threads ADD COLUMN legal_skills TEXT NOT NULL DEFAULT '[]';
        """,
    ),
    (
        21,
        "extend thread scenes for cases and litigation",
        """
        ALTER TABLE threads RENAME COLUMN scene TO previous_scene;
        ALTER TABLE threads ADD COLUMN scene TEXT NOT NULL DEFAULT 'general'
            CHECK (scene IN ('general', 'legal-retrieval', 'case-retrieval', 'litigation-work'));
        UPDATE threads SET scene = previous_scene;
        ALTER TABLE threads DROP COLUMN previous_scene;
        """,
    ),
)


MIGRATIONS = (*MIGRATIONS, (22, "local markdown knowledge sources", KNOWLEDGE_SCHEMA))

MIGRATIONS = (*MIGRATIONS, (23, "create context compactions", """
CREATE TABLE context_compactions (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    boundary_sequence INTEGER NOT NULL,
    summary_text TEXT NOT NULL,
    kept_ratio REAL NOT NULL,
    degraded INTEGER NOT NULL CHECK (degraded IN (0, 1)),
    model TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_context_compactions_thread ON context_compactions(thread_id, boundary_sequence);
"""))

MIGRATIONS = (*MIGRATIONS, (24, "local file redaction", """
CREATE TABLE redaction_jobs (
 id TEXT PRIMARY KEY,
 filename TEXT NOT NULL,
 created_at TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1,
 canonical BLOB NOT NULL,
 digest TEXT NOT NULL
);
CREATE TABLE redaction_mappings (
 job_id TEXT PRIMARY KEY REFERENCES redaction_jobs(id) ON DELETE CASCADE,
 payload TEXT NOT NULL,
 digest TEXT NOT NULL
);
"""))

MIGRATIONS = (*MIGRATIONS, (25, "contract review scene and records", """
ALTER TABLE threads RENAME COLUMN scene TO previous_scene;
ALTER TABLE threads ADD COLUMN scene TEXT NOT NULL DEFAULT 'general'
    CHECK (scene IN ('general', 'legal-retrieval', 'case-retrieval', 'litigation-work', 'contract-review'));
UPDATE threads SET scene = previous_scene;
ALTER TABLE threads DROP COLUMN previous_scene;
CREATE TABLE contract_reviews (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    file_digest TEXT NOT NULL,
    original BLOB NOT NULL,
    skill TEXT NOT NULL,
    party TEXT NOT NULL CHECK (party IN ('party_a', 'party_b', 'neutral')),
    strictness TEXT NOT NULL CHECK (strictness IN ('loose', 'normal', 'strict')),
    signature TEXT NOT NULL,
    retrieval_enabled INTEGER NOT NULL CHECK (retrieval_enabled IN (0, 1)),
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed', 'stopped')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_contract_reviews_thread ON contract_reviews(thread_id);
CREATE INDEX idx_contract_reviews_status ON contract_reviews(status);
CREATE TABLE review_opinions (
    id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL REFERENCES contract_reviews(id) ON DELETE CASCADE,
    clause_label TEXT NOT NULL,
    clause_excerpt TEXT NOT NULL,
    anchor_text TEXT NOT NULL,
    occurrence INTEGER,
    level TEXT NOT NULL,
    category TEXT NOT NULL,
    body TEXT NOT NULL,
    suggestion TEXT NOT NULL DEFAULT '',
    adopted INTEGER NOT NULL DEFAULT 1 CHECK (adopted IN (0, 1)),
    edited_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_review_opinions_review ON review_opinions(review_id);
CREATE TABLE opinion_citations (
    id TEXT PRIMARY KEY,
    opinion_id TEXT NOT NULL REFERENCES review_opinions(id) ON DELETE CASCADE,
    document TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('mcp', 'knowledge', 'web')),
    source_label TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_opinion_citations_opinion ON opinion_citations(opinion_id);
"""))

# Reuse the v15 attachment-table rebuild, preserving all columns and indexes.
_DOC_IMPORT_SCHEMA = MIGRATIONS[14][2].split("        CREATE TABLE attachment_blobs_v15", 1)[1].split("        CREATE TABLE artifact_blobs", 1)[0]
_DOC_IMPORT_SCHEMA = ("CREATE TABLE attachment_blobs_v15" + _DOC_IMPORT_SCHEMA).replace("_v15", "_v26").replace("REFERENCES attachment_blobs_v26", "REFERENCES attachment_blobs").replace("'image/webp',", "'image/webp', 'application/msword',")
MIGRATIONS = (*MIGRATIONS, (26, "preserve normalized contract import", "PRAGMA legacy_alter_table=ON;\nALTER TABLE contract_reviews ADD COLUMN normalized_docx BLOB;\n" + _DOC_IMPORT_SCHEMA + """
PRAGMA legacy_alter_table=OFF;
CREATE TRIGGER doc_import_thread_artifacts_delete BEFORE DELETE ON threads
BEGIN DELETE FROM artifacts WHERE thread_id=OLD.id; END;
CREATE TRIGGER doc_import_turn_artifacts_delete BEFORE DELETE ON turns
BEGIN DELETE FROM artifacts WHERE turn_id=OLD.id; END;
"""))

MIGRATIONS = (*MIGRATIONS, (27, "local scheduled tasks", """
ALTER TABLE threads RENAME COLUMN scene TO previous_scene;
ALTER TABLE threads ADD COLUMN scene TEXT NOT NULL DEFAULT 'general'
 CHECK (scene IN ('general','legal-retrieval','case-retrieval','litigation-work','contract-review','scheduled'));
UPDATE threads SET scene=previous_scene;
ALTER TABLE threads DROP COLUMN previous_scene;
CREATE TABLE scheduled_tasks (
 id TEXT PRIMARY KEY, data TEXT NOT NULL CHECK(json_valid(data)),
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), next_run TEXT, created_at TEXT NOT NULL
);
CREATE INDEX idx_scheduled_tasks_due ON scheduled_tasks(enabled,next_run);
CREATE TABLE scheduled_task_runs (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES scheduled_tasks(id) ON DELETE CASCADE,
 thread_id TEXT REFERENCES threads(id) ON DELETE SET NULL, turn_id TEXT,
 trigger_kind TEXT NOT NULL CHECK(trigger_kind IN ('schedule','catchup','manual')),
 status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','interrupted')),
 started_at TEXT NOT NULL, finished_at TEXT, error TEXT
);
CREATE INDEX idx_scheduled_runs_task ON scheduled_task_runs(task_id,started_at DESC);
CREATE UNIQUE INDEX idx_scheduled_one_running ON scheduled_task_runs((1)) WHERE status='running';
"""))

# Contract editing owns migration 28; parallel legal features append after it.
MIGRATIONS = (*MIGRATIONS, (28, "contract editing copies and turn snapshots", """
CREATE TABLE contract_editor_states (
 review_id TEXT PRIMARY KEY REFERENCES contract_reviews(id) ON DELETE CASCADE,
 version INTEGER NOT NULL CHECK(version >= 0),
 data TEXT NOT NULL CHECK(json_valid(data))
);
CREATE TABLE contract_editor_turns (
 turn_id TEXT PRIMARY KEY REFERENCES turns(id) ON DELETE CASCADE,
 review_id TEXT NOT NULL REFERENCES contract_reviews(id) ON DELETE CASCADE,
 thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
 snapshot TEXT NOT NULL CHECK(json_valid(snapshot))
);
CREATE INDEX idx_contract_editor_turns_review ON contract_editor_turns(review_id);
"""))

MIGRATIONS = (*MIGRATIONS, (29, "independent case calendar", """
CREATE TABLE case_calendar_events (
 id TEXT PRIMARY KEY,
 revision INTEGER NOT NULL CHECK(revision >= 1),
 title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 120),
 kind TEXT NOT NULL CHECK(kind IN ('hearing', 'deadline', 'meeting', 'other')),
 event_date TEXT NOT NULL,
 event_time TEXT,
 time_zone TEXT NOT NULL,
 case_name TEXT NOT NULL DEFAULT '' CHECK(length(case_name) <= 200),
 location TEXT NOT NULL DEFAULT '' CHECK(length(location) <= 300),
 notes TEXT NOT NULL DEFAULT '' CHECK(length(notes) <= 5000),
 reminder_minutes INTEGER CHECK(reminder_minutes IN (0, 15, 60, 1440, 4320, 10080)),
 reminder_at TEXT,
 reminder_acknowledged_at TEXT,
 reminder_claimed_at TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX idx_case_calendar_date ON case_calendar_events(event_date, event_time);
CREATE INDEX idx_case_calendar_reminders ON case_calendar_events(reminder_at)
 WHERE reminder_acknowledged_at IS NULL AND reminder_at IS NOT NULL;
"""))

MIGRATIONS = (*MIGRATIONS, (30, "beijing calendar and custom event types", """
CREATE TABLE case_calendar_events_v30 (
 id TEXT PRIMARY KEY,
 revision INTEGER NOT NULL CHECK(revision >= 1),
 title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 120),
 kind TEXT NOT NULL CHECK(kind IN ('hearing', 'deadline', 'meeting', 'interview', 'mediation', 'filing', 'investigation', 'evidence-deadline', 'appeal-deadline', 'enforcement-deadline', 'preservation-expiry', 'other')),
 event_date TEXT NOT NULL,
 event_time TEXT,
 time_zone TEXT NOT NULL,
 case_name TEXT NOT NULL DEFAULT '' CHECK(length(case_name) <= 200),
 location TEXT NOT NULL DEFAULT '' CHECK(length(location) <= 300),
 notes TEXT NOT NULL DEFAULT '' CHECK(length(notes) <= 5000),
 reminder_minutes INTEGER CHECK(reminder_minutes IN (0, 15, 60, 1440, 4320, 10080)),
 reminder_at TEXT,
 reminder_acknowledged_at TEXT,
 reminder_claimed_at TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 custom_kind TEXT NOT NULL DEFAULT '' CHECK(length(custom_kind) <= 40)
);
INSERT INTO case_calendar_events_v30 SELECT *, '' FROM case_calendar_events;
DROP TABLE case_calendar_events;
ALTER TABLE case_calendar_events_v30 RENAME TO case_calendar_events;
CREATE INDEX idx_case_calendar_date ON case_calendar_events(event_date, event_time);
CREATE INDEX idx_case_calendar_reminders ON case_calendar_events(reminder_at)
 WHERE reminder_acknowledged_at IS NULL AND reminder_at IS NOT NULL;
"""))


def _migrate_calendar_beijing(connection: sqlite3.Connection) -> None:
    # Frozen v30 conversion: preserve timed instants, retain all-day dates.
    # Claims/acknowledgements survive, so an upgrade never replays a notice.
    beijing = timezone(timedelta(hours=8))
    for row in connection.execute("SELECT * FROM case_calendar_events").fetchall():
        day, clock = row["event_date"], row["event_time"]
        if clock is not None:
            old = datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=ZoneInfo(row["time_zone"]), fold=0)
            converted = old.astimezone(beijing)
            day, clock = converted.date().isoformat(), converted.strftime("%H:%M")
        reminder = row["reminder_at"]
        if clock is None and row["reminder_minutes"] is not None:
            due = datetime.fromisoformat(f"{day}T09:00").replace(tzinfo=beijing) - timedelta(minutes=row["reminder_minutes"])
            reminder = due.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        if (day, clock, "Asia/Shanghai", reminder) != (row["event_date"], row["event_time"], row["time_zone"], row["reminder_at"]):
            connection.execute(
                "UPDATE case_calendar_events SET event_date=?, event_time=?, time_zone='Asia/Shanghai', reminder_at=?, revision=revision+1, updated_at=? WHERE id=?",
                (day, clock, reminder, _utc_now(), row["id"]),
            )


# Migrations that must run on a connection with foreign-key enforcement
# disabled. SQLite cannot ``ADD CONSTRAINT``, so rebuilding ``threads`` to gain
# a ``workspace_id`` foreign key would otherwise cascade-delete its dependent
# ``turns``/``messages``/``events`` rows via the implicit ``DELETE`` that
# ``DROP TABLE`` performs when foreign keys are on.
MIGRATIONS = (*MIGRATIONS, (31, "track replaced conversation turns", """
ALTER TABLE threads ADD COLUMN history_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE context_compactions ADD COLUMN history_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE turns ADD COLUMN superseded_by TEXT REFERENCES turns(id) ON DELETE SET NULL;
CREATE INDEX turns_superseded_by_idx ON turns(superseded_by);
"""))


# Full DOCX working copies are local and do not require an AI thread.
MIGRATIONS = (*MIGRATIONS, (32, "embedded contract Word documents", """
CREATE TABLE contract_word_documents (
 id TEXT PRIMARY KEY,
 filename TEXT NOT NULL,
 original BLOB NOT NULL,
 content BLOB NOT NULL,
 revision INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL
);
ALTER TABLE contract_reviews ADD COLUMN document_id TEXT REFERENCES contract_word_documents(id);
"""))

MIGRATIONS = (*MIGRATIONS, (33, "native Word review markup", """
ALTER TABLE review_opinions ADD COLUMN replacement_text TEXT;
ALTER TABLE contract_word_documents ADD COLUMN review_markups TEXT NOT NULL DEFAULT '{}';
"""))


MIGRATIONS = (*MIGRATIONS, (34, "contract review requirements", """
ALTER TABLE contract_reviews ADD COLUMN requirements TEXT NOT NULL DEFAULT '';
"""))

MIGRATIONS = (*MIGRATIONS, (35, "contract reference tool call receipts", """
CREATE TABLE opinion_citations_v35 (
    id TEXT PRIMARY KEY,
    opinion_id TEXT NOT NULL REFERENCES review_opinions(id) ON DELETE CASCADE,
    document TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('mcp', 'knowledge', 'web', 'tool')),
    source_label TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source_call_id TEXT NOT NULL DEFAULT ''
);
INSERT INTO opinion_citations_v35 (id, opinion_id, document, source_kind, source_label, verified_at, created_at)
SELECT id, opinion_id, document, source_kind, source_label, verified_at, created_at FROM opinion_citations;
DROP TABLE opinion_citations;
ALTER TABLE opinion_citations_v35 RENAME TO opinion_citations;
CREATE INDEX idx_opinion_citations_opinion ON opinion_citations(opinion_id);
"""))


MIGRATIONS = (*MIGRATIONS, (36, "registered litigation internal files", """
CREATE TABLE litigation_internal_files (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY (workspace_id, relative_path)
);
"""))

_FOREIGN_KEYS_OFF_MIGRATIONS = frozenset({4, 15, 26})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _statements(script: str):
    statement = ""
    for line in script.splitlines():
        statement += f"{line}\n"
        if sqlite3.complete_statement(statement):
            yield statement
            statement = ""
    if statement.strip():
        raise sqlite3.OperationalError("incomplete migration statement")


def _applied_migrations(connection: sqlite3.Connection) -> dict[int, str]:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if table is None:
        return {}
    return {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT version, name FROM schema_migrations"
        ).fetchall()
    }


def _validate_migration_history(applied: dict[int, str]) -> None:
    expected = {version: name for version, name, _ in MIGRATIONS}
    if any(expected.get(version) != name for version, name in applied.items()):
        raise sqlite3.DatabaseError("database migration history is incompatible")


def _normalized_sql(value: str | None) -> str | None:
    return None if value is None else " ".join(value.split())


def _provider_schema_fingerprint(connection: sqlite3.Connection) -> tuple:
    table = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'providers'"
    ).fetchone()
    table_sql = None if table is None else _normalized_sql(table[0])
    columns = tuple(
        tuple(row)
        for row in connection.execute("PRAGMA table_info(providers)").fetchall()
    )
    indexes = []
    for row in connection.execute("PRAGMA index_list(providers)").fetchall():
        name = row[1]
        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (name,),
        ).fetchone()
        indexed_columns = tuple(
            tuple(column)
            for column in connection.execute(
                "SELECT seqno, cid, name FROM pragma_index_info(?) ORDER BY seqno",
                (name,),
            ).fetchall()
        )
        indexes.append(
            (
                name,
                row[2],
                row[3],
                row[4],
                indexed_columns,
                None if definition is None else _normalized_sql(definition[0]),
            )
        )
    return table_sql, columns, tuple(sorted(indexes))


@lru_cache(maxsize=1)
def _expected_provider_schema_fingerprint() -> tuple:
    connection = sqlite3.connect(":memory:")
    try:
        for statement in _statements(MIGRATIONS[0][2]):
            connection.execute(statement)
        return _provider_schema_fingerprint(connection)
    finally:
        connection.close()


def _validate_provider_schema(connection: sqlite3.Connection) -> None:
    if _provider_schema_fingerprint(
        connection
    ) != _expected_provider_schema_fingerprint():
        raise sqlite3.DatabaseError("database provider schema is incompatible")


def _apply_migration(
    database: Database,
    version: int,
    name: str,
    sql: str,
    *,
    foreign_keys: bool,
) -> None:
    with database.connect(foreign_keys=foreign_keys) as connection:
        connection.execute("BEGIN")
        try:
            for statement in _statements(sql):
                connection.execute(statement)
            if version == 30:
                _migrate_calendar_beijing(connection)
            connection.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) "
                "VALUES (?, ?, ?)",
                (version, name, _utc_now()),
            )
            connection.commit()
        except sqlite3.DatabaseError:
            connection.rollback()
            raise


def run_migrations(database: Database) -> None:
    with database.connect() as connection:
        applied = _applied_migrations(connection)
    _validate_migration_history(applied)

    # Validate the foundational table before later migrations attempt to
    # reference it. This keeps corruption/incompatible schemas diagnosable
    # instead of surfacing as an unrelated foreign-key error.
    if applied.get(1) == "create providers":
        with database.connect() as connection:
            _validate_provider_schema(connection)

    for version, name, sql in MIGRATIONS:
        if version in applied:
            continue
        _apply_migration(
            database,
            version,
            name,
            sql,
            foreign_keys=version not in _FOREIGN_KEYS_OFF_MIGRATIONS,
        )
        applied[version] = name

    if applied.get(1) == "create providers":
        with database.connect() as connection:
            _validate_provider_schema(connection)

    if 15 in applied:
        with database.connect() as connection:
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.DatabaseError("database foreign key check failed")

    # WAL is a persistent database setting. Configure it once at startup;
    # renegotiating it on every short-lived streaming write creates avoidable
    # lock/file churn while model deltas are arriving rapidly.
    with database.connect() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
