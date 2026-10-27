import pytest

from verilex_runtime.sessions.errors import SessionDomainError
from verilex_runtime.sessions.workspace_service import WorkspaceService
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.workspace_repository import WorkspaceRepository


@pytest.fixture
def service(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    ids = iter(f"id-{n}" for n in range(1, 1000))
    return WorkspaceService(
        WorkspaceRepository(database),
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-13T00:00:00Z",
    )


def test_create_and_get_workspace(service, tmp_path):
    root = tmp_path / "authorized"
    root.mkdir()
    record = service.create(str(root), "Authorized")
    assert record.root_path == str(root.resolve())
    assert record.allow_read is True
    assert record.allow_write is True
    assert service.get(record.id).id == record.id


def test_create_rejects_duplicate_root(service, tmp_path):
    root = tmp_path / "authorized"
    root.mkdir()
    service.create(str(root), "Authorized")
    with pytest.raises(SessionDomainError) as captured:
        service.create(str(root), "Duplicate")
    assert captured.value.code == "workspace_root_path_exists"


def test_create_rejects_relative_path(service):
    with pytest.raises(SessionDomainError) as captured:
        service.create("relative/path", "Relative")
    assert captured.value.code == "workspace_root_path_invalid"


def test_create_rejects_missing_directory(service, tmp_path):
    with pytest.raises(SessionDomainError) as captured:
        service.create(str(tmp_path / "does-not-exist"), "Missing")
    assert captured.value.code == "workspace_root_path_invalid"


def test_list_and_update_permissions(service, tmp_path):
    root = tmp_path / "authorized"
    root.mkdir()
    record = service.create(str(root), "Authorized")
    assert [w.id for w in service.list()] == [record.id]

    updated = service.update_permissions(
        record.id, allow_read=False, allow_write=True
    )
    assert updated.allow_read is False
    assert updated.allow_write is True
    assert service.get(record.id).allow_write is True


def test_revoke_removes_workspace(service, tmp_path):
    root = tmp_path / "authorized"
    root.mkdir()
    record = service.create(str(root), "Authorized")
    service.revoke(record.id)
    assert service.list() == []
    with pytest.raises(SessionDomainError) as captured:
        service.get(record.id)
    assert captured.value.code == "workspace_not_found"


def test_get_missing_workspace_raises(service):
    with pytest.raises(SessionDomainError) as captured:
        service.get("missing")
    assert captured.value.code == "workspace_not_found"
