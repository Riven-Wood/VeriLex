"""Migration 22: local source metadata, rebuildable index and durable quotes."""

KNOWLEDGE_SCHEMA = """
CREATE TABLE knowledge_libraries (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, root TEXT NOT NULL UNIQUE,
    device INTEGER NOT NULL, inode INTEGER NOT NULL,
    excluded TEXT NOT NULL DEFAULT '[]', directories TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending', error TEXT, synced_at TEXT,
    detached INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE knowledge_documents (
    id TEXT PRIMARY KEY,
    library_id TEXT NOT NULL REFERENCES knowledge_libraries(id) ON DELETE CASCADE,
    path TEXT NOT NULL, version TEXT NOT NULL, raw BLOB NOT NULL, body TEXT NOT NULL,
    status TEXT NOT NULL, error TEXT, UNIQUE(library_id, path)
);
CREATE INDEX knowledge_document_library ON knowledge_documents(library_id, status);
CREATE VIRTUAL TABLE knowledge_fts USING fts5(
    path, body, content='knowledge_documents', content_rowid='rowid',
    tokenize='trigram case_sensitive 1'
);
CREATE TRIGGER knowledge_insert AFTER INSERT ON knowledge_documents BEGIN
    INSERT INTO knowledge_fts(rowid,path,body) VALUES(new.rowid,new.path,new.body);
END;
CREATE TRIGGER knowledge_delete AFTER DELETE ON knowledge_documents BEGIN
    INSERT INTO knowledge_fts(knowledge_fts,rowid,path,body)
        VALUES('delete',old.rowid,old.path,old.body);
END;
CREATE TRIGGER knowledge_update AFTER UPDATE ON knowledge_documents BEGIN
    INSERT INTO knowledge_fts(knowledge_fts,rowid,path,body)
        VALUES('delete',old.rowid,old.path,old.body);
    INSERT INTO knowledge_fts(rowid,path,body) VALUES(new.rowid,new.path,new.body);
END;
CREATE TABLE knowledge_quotes (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    library_id TEXT NOT NULL, document_id TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""
