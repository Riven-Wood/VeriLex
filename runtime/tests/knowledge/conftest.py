import pytest
from verilex_runtime.knowledge.service import KnowledgeService
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations

@pytest.fixture
def library(tmp_path):
    db = Database(tmp_path / 'index.sqlite3')
    run_migrations(db)
    root = tmp_path / 'notes'
    root.mkdir()
    service = KnowledgeService(db)
    identifier = service.associate(str(root))
    return service, identifier, root
