"""Upgrade populated databases without losing the existing legal work."""
import sqlite3

import pytest
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import MIGRATIONS, run_migrations


def test_v21_preserves_thread_choices_messages_and_foreign_keys(tmp_path, monkeypatch):
    db = Database(tmp_path / 'upgrade.sqlite3')
    with monkeypatch.context() as patch:
        patch.setattr('verilex_runtime.storage.migrations.MIGRATIONS', MIGRATIONS[:20])
        run_migrations(db)
    with db.connect() as connection:
        connection.execute("INSERT INTO threads (id,title,model,status,created_at,updated_at,scene,legal_channel,legal_skills) VALUES ('old','已验收检索','model','idle','now','now','legal-retrieval','plugin','[\"检索\"]')")
        connection.execute("INSERT INTO turns (id,thread_id,status,created_at) VALUES ('turn','old','completed','now')")
        connection.execute("INSERT INTO messages (id,thread_id,turn_id,sequence,role,content_json,created_at) VALUES ('msg','old','turn',1,'user','历史问题','now')")
    run_migrations(db)
    run_migrations(db)
    with db.connect() as connection:
        row = connection.execute('SELECT scene,legal_channel,legal_skills FROM threads').fetchone()
        assert tuple(row) == ('legal-retrieval', 'plugin', '["检索"]')
        assert connection.execute('SELECT content_json FROM messages').fetchone()[0] == '历史问题'
        assert connection.execute('SELECT thread_id FROM turns').fetchone()[0] == 'old'
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        for scene in ('case-retrieval', 'litigation-work'):
            connection.execute('UPDATE threads SET scene=?', (scene,))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE threads SET scene='unknown'")
