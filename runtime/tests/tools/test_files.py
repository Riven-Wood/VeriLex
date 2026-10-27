import os

import pytest

from verilex_runtime.storage.models import RiskLevel
from verilex_runtime.tools import (
    ToolContext,
    ToolError,
    ToolRegistry,
    register_file_tools,
    register_skill_tools,
)
from verilex_runtime.tools.files import resolve_within_root
from verilex_runtime.tools import registry as tool_registry


def test_sandbox_profile_denies_network_and_confines_writes(tmp_path):
    from verilex_runtime.tools.sandbox import build_sandbox_profile

    profile = build_sandbox_profile(tmp_path / "ws")
    assert "(deny default)" in profile
    assert "(deny network*)" in profile
    assert "(deny file-write*)" in profile
    assert str(tmp_path / "ws") in profile


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['copy_path', 'move_path'])
async def test_reject_destination_inside_source(ctx, registry, root, operation, monkeypatch):
    def must_not_copy(*args):
        pytest.fail('A descendant destination must be rejected before copying')
    monkeypatch.setattr('verilex_runtime.tools.files._copy_tree', must_not_copy)
    result = await _run(registry, ctx, operation,
                        {'source': 'sub', 'destination': 'sub/backup/deep'})
    assert not result.ok and 'destination_inside_source' in result.output
    assert not (root / 'sub/backup').exists()
    assert (root / 'sub/b.txt').read_text() == 'nested content\n'


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['move_path', 'delete_path'])
@pytest.mark.parametrize('directory_link', [False, True])
async def test_cross_volume_rejects_nested_links_before_copy(
    ctx, registry, root, outside, trash, monkeypatch, operation, directory_link
):
    import errno
    _patch_delete(monkeypatch, trash)
    target = outside if directory_link else outside / 'secret.txt'
    (root / 'sub/link').symlink_to(target, target_is_directory=directory_link)
    def cross_volume(*args):
        raise OSError(errno.EXDEV, 'cross-device link')
    monkeypatch.setattr('verilex_runtime.tools.files.os.rename', cross_volume)
    args = ({'source': 'sub', 'destination': 'moved'} if operation == 'move_path'
            else {'path': 'sub', 'recursive': True})
    result = await _run(registry, ctx, operation, args)
    assert not result.ok and 'symlink_not_supported' in result.output
    assert (root / 'sub/link').is_symlink()
    assert (root / 'sub/b.txt').exists()
    assert not (root / 'moved').exists()
    assert not list(trash.iterdir())
    assert (outside / 'secret.txt').read_text() == 'top secret\n'


def test_sandbox_command_wraps_when_available(tmp_path):
    import shutil

    from verilex_runtime.tools.sandbox import build_sandbox_command

    argv = ["echo", "hi"]
    if shutil.which("sandbox-exec") is not None:
        wrapped = build_sandbox_command(argv, tmp_path)
        assert wrapped[:2] == ["/usr/bin/sandbox-exec", "-p"]
        # argv is preserved verbatim after the -p profile.
        assert wrapped[3:] == argv
    else:
        # Degraded path: no wrapper, argv returned unchanged.
        assert build_sandbox_command(argv, tmp_path) == argv


@pytest.fixture
def root(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("hello world\n", encoding="utf-8")
    (workspace / "sub").mkdir()
    (workspace / "sub" / "b.txt").write_text("nested content\n", encoding="utf-8")
    return workspace


@pytest.fixture
def outside(tmp_path):
    directory = tmp_path / "outside"
    directory.mkdir()
    (directory / "secret.txt").write_text("top secret\n", encoding="utf-8")
    return directory


@pytest.fixture
def ctx(root):
    return ToolContext(root_path=root, allow_read=True, allow_write=True)


def test_direct_tool_context_defaults_to_no_workspace_identity(tmp_path):
    context = ToolContext(
        root_path=tmp_path,
        allow_read=True,
        allow_write=True,
    )

    assert context.workspace_id is None


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_file_tools(reg)
    return reg


def _definition(registry, name):
    return registry.get(name)


def test_resolve_allows_inside_path(root):
    resolved = resolve_within_root(root, "a.txt")
    assert resolved == (root / "a.txt").resolve()


def test_resolve_rejects_parent_traversal(root):
    with pytest.raises(ToolError) as captured:
        resolve_within_root(root, "../outside/secret.txt")
    assert captured.value.code == "path_out_of_bounds"


def test_resolve_rejects_absolute_escape(root):
    with pytest.raises(ToolError) as captured:
        resolve_within_root(root, str(root.parent / "outside-synthetic.txt"))
    assert captured.value.code == "path_out_of_bounds"


def test_resolve_rejects_symlink_escape(root, outside):
    link = root / "link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ToolError) as captured:
        resolve_within_root(root, "link")
    assert captured.value.code == "path_out_of_bounds"


def test_resolve_rejects_deep_traversal(root):
    with pytest.raises(ToolError) as captured:
        resolve_within_root(root, "sub/../../outside")
    assert captured.value.code == "path_out_of_bounds"


@pytest.mark.asyncio
async def test_list_directory_lists_direct_children(ctx, registry):
    result = await _definition(registry, "list_directory").execute(ctx, {"path": "."})
    assert result.ok is True
    assert "a.txt" in result.output
    assert "sub/" in result.output


@pytest.mark.asyncio
async def test_read_file_returns_content(ctx, registry):
    result = await _definition(registry, "read_file").execute(ctx, {"path": "a.txt"})
    assert result.ok is True
    assert result.output == "hello world"


@pytest.mark.asyncio
async def test_read_file_rejects_out_of_bounds(ctx, registry, outside):
    with pytest.raises(ToolError) as captured:
        await _definition(registry, "read_file").execute(
            ctx, {"path": str(outside / "secret.txt")}
        )
    assert captured.value.code == "path_out_of_bounds"


@pytest.mark.asyncio
async def test_write_file_creates_inside(ctx, registry, root):
    result = await _definition(registry, "write_file").execute(
        ctx, {"path": "new.txt", "content": "brand new"}
    )
    assert result.ok is True
    assert (root / "new.txt").read_text(encoding="utf-8") == "brand new"


@pytest.mark.asyncio
async def test_write_file_rejects_existing(ctx, registry):
    with pytest.raises(ToolError) as captured:
        await _definition(registry, "write_file").execute(
            ctx, {"path": "a.txt", "content": "overwrite"}
        )
    assert captured.value.code == "file_exists"


@pytest.mark.asyncio
async def test_write_file_rejects_escape(ctx, registry):
    with pytest.raises(ToolError) as captured:
        await _definition(registry, "write_file").execute(
            ctx, {"path": "../outside/pwn.txt", "content": "x"}
        )
    assert captured.value.code == "path_out_of_bounds"


@pytest.mark.asyncio
async def test_apply_file_patch_replaces_text(ctx, registry, root):
    result = await _definition(registry, "apply_file_patch").execute(
        ctx, {"path": "a.txt", "old_text": "world", "new_text": "there"}
    )
    assert result.ok is True
    assert (root / "a.txt").read_text(encoding="utf-8") == "hello there\n"


@pytest.mark.asyncio
async def test_apply_file_patch_rejects_ambiguous(ctx, registry):
    (ctx.root_path / "dup.txt").write_text("x x", encoding="utf-8")
    with pytest.raises(ToolError) as captured:
        await _definition(registry, "apply_file_patch").execute(
            ctx, {"path": "dup.txt", "old_text": "x", "new_text": "y"}
        )
    assert captured.value.code == "patch_ambiguous"


@pytest.mark.asyncio
async def test_search_files_finds_text(ctx, registry):
    result = await _definition(registry, "search_files").execute(
        ctx, {"path": ".", "query": "nested"}
    )
    assert result.ok is True
    assert "sub/b.txt" in result.output


@pytest.mark.asyncio
async def test_write_tool_requires_write_permission(ctx, registry):
    read_only = ToolContext(root_path=ctx.root_path, allow_read=True, allow_write=False)
    with pytest.raises(ToolError) as captured:
        await _definition(registry, "write_file").execute(
            read_only, {"path": "nope.txt", "content": "x"}
        )
    assert captured.value.code == "write_not_allowed"


def test_file_tools_have_expected_risk_levels(registry):
    assert _definition(registry, "list_directory").risk_level is RiskLevel.LOW
    assert _definition(registry, "search_files").risk_level is RiskLevel.LOW
    assert _definition(registry, "read_file").risk_level is RiskLevel.LOW
    assert _definition(registry, "write_file").risk_level is RiskLevel.HIGH
    assert _definition(registry, "apply_file_patch").risk_level is RiskLevel.HIGH


def test_file_tools_describe_exact_capabilities(registry):
    expected = {
        "list_directory": (tool_registry.ToolScope.READ, RiskLevel.LOW),
        "search_files": (tool_registry.ToolScope.READ, RiskLevel.LOW),
        "read_file": (tool_registry.ToolScope.READ, RiskLevel.LOW),
        "write_file": (tool_registry.ToolScope.WRITE, RiskLevel.HIGH),
        "apply_file_patch": (tool_registry.ToolScope.WRITE, RiskLevel.HIGH),
    }

    for name, (scope, risk_level) in expected.items():
        definition = _definition(registry, name)
        assert definition is not None
        assert definition.source is tool_registry.ToolSource.BUILTIN
        assert definition.category is tool_registry.ToolCategory.FILES
        assert definition.scopes == frozenset({scope})
        assert definition.requires_workspace is True
        assert definition.source_name is None
        assert definition.risk_level is risk_level


def test_registry_validation_rejects_unknown_and_missing_args(registry):
    assert registry.validate("nope", {}) == "unknown tool: nope"
    assert "missing required argument" in registry.validate("read_file", {})
    assert registry.validate("read_file", {"path": "a.txt"}) is None


def test_os_module_available_for_path_checks():
    # Sanity: the path checks rely on filesystem resolution, not the network.
    assert os.name in ("posix", "nt")


# ---------------------------------------------------------------------------
# copy_path / move_path / delete_path (safe file operations)


@pytest.fixture
def trash(tmp_path):
    trash_root = tmp_path / "Trash"
    trash_root.mkdir()
    return trash_root


def _patch_delete(monkeypatch, trash_root):
    monkeypatch.setattr(
        "verilex_runtime.tools.files._default_trash_root",
        lambda: trash_root,
    )


async def _run(registry, ctx, name, args):
    return await registry.get(name).execute(ctx, args)


@pytest.mark.asyncio
async def test_copy_path_copies_file_and_directory_tree(ctx, registry, root):
    result = await _run(
        registry,
        ctx,
        "copy_path",
        {"source": "a.txt", "destination": "copies/a.txt"},
    )
    assert result.ok is True
    assert (root / "copies" / "a.txt").read_text(encoding="utf-8") == "hello world\n"
    assert (root / "a.txt").exists()

    result = await _run(
        registry,
        ctx,
        "copy_path",
        {"source": "sub", "destination": "sub-copy"},
    )
    assert result.ok is True
    assert (root / "sub-copy" / "b.txt").read_text(encoding="utf-8") == (
        "nested content\n"
    )


@pytest.mark.asyncio
async def test_copy_path_enforces_injected_limits_with_zero_leftovers(
    ctx, registry, root, monkeypatch
):
    monkeypatch.setattr("verilex_runtime.tools.files.COPY_MAX_BYTES", 4)
    result = await _run(
        registry,
        ctx,
        "copy_path",
        {"source": "a.txt", "destination": "copy.txt"},
    )
    assert result.ok is False
    assert "copy_limit_exceeded" in result.output
    assert not (root / "copy.txt").exists()
    assert (root / "a.txt").exists()

    monkeypatch.setattr("verilex_runtime.tools.files.COPY_MAX_BYTES", 10 * 1024 * 1024)
    monkeypatch.setattr("verilex_runtime.tools.files.COPY_MAX_ENTRIES", 1)
    result = await _run(
        registry,
        ctx,
        "copy_path",
        {"source": "sub", "destination": "sub-copy"},
    )
    assert result.ok is False
    assert "copy_limit_exceeded" in result.output
    assert not (root / "sub-copy").exists()


@pytest.mark.asyncio
async def test_move_path_renames_moves_directories(ctx, registry, root):
    result = await _run(
        registry,
        ctx,
        "move_path",
        {"source": "a.txt", "destination": "renamed.txt"},
    )
    assert result.ok is True
    assert (root / "renamed.txt").exists()
    assert not (root / "a.txt").exists()

    (root / "target-dir").mkdir()
    result = await _run(
        registry,
        ctx,
        "move_path",
        {"source": "renamed.txt", "destination": "target-dir/moved.txt"},
    )
    assert result.ok is True
    assert (root / "target-dir" / "moved.txt").exists()

    result = await _run(
        registry,
        ctx,
        "move_path",
        {"source": "sub", "destination": "target-dir/sub"},
    )
    assert result.ok is True
    assert (root / "target-dir" / "sub" / "b.txt").exists()
    assert not (root / "sub").exists()


@pytest.mark.asyncio
async def test_delete_path_moves_file_to_injected_trash(
    ctx, registry, root, trash, monkeypatch
):
    _patch_delete(monkeypatch, trash)
    result = await _run(registry, ctx, "delete_path", {"path": "a.txt"})
    assert result.ok is True
    assert not (root / "a.txt").exists()
    assert (trash / "a.txt").read_text(encoding="utf-8") == "hello world\n"


@pytest.mark.asyncio
async def test_delete_path_directory_rules(
    ctx, registry, root, trash, monkeypatch
):
    _patch_delete(monkeypatch, trash)
    (root / "empty-dir").mkdir()

    result = await _run(registry, ctx, "delete_path", {"path": "empty-dir"})
    assert result.ok is True
    assert not (root / "empty-dir").exists()
    assert (trash / "empty-dir").is_dir()

    result = await _run(registry, ctx, "delete_path", {"path": "sub"})
    assert result.ok is False
    assert "directory_not_empty" in result.output
    assert (root / "sub" / "b.txt").exists()

    result = await _run(
        registry, ctx, "delete_path", {"path": "sub", "recursive": True}
    )
    assert result.ok is True
    assert not (root / "sub").exists()
    assert (trash / "sub" / "b.txt").read_text(encoding="utf-8") == "nested content\n"


@pytest.mark.asyncio
async def test_delete_path_trash_name_conflict_appends_suffix(
    ctx, registry, root, trash, monkeypatch
):
    _patch_delete(monkeypatch, trash)
    (trash / "a.txt").write_text("precious\n", encoding="utf-8")

    result = await _run(registry, ctx, "delete_path", {"path": "a.txt"})

    assert result.ok is True
    assert (trash / "a.txt").read_text(encoding="utf-8") == "precious\n"
    moved = [item for item in trash.iterdir() if item.name != "a.txt"]
    assert len(moved) == 1
    assert moved[0].name.startswith("a.txt-")
    assert moved[0].read_text(encoding="utf-8") == "hello world\n"


@pytest.mark.asyncio
async def test_delete_path_cross_device_falls_back_to_limited_copy(
    ctx, registry, root, trash, monkeypatch
):
    import errno

    _patch_delete(monkeypatch, trash)
    real_rename = os.rename

    def rename_raises_exdev(src, dst):
        if str(dst).startswith(str(trash)):
            raise OSError(errno.EXDEV, "cross-device link")
        return real_rename(src, dst)

    monkeypatch.setattr("verilex_runtime.tools.files.os.rename", rename_raises_exdev)

    result = await _run(registry, ctx, "delete_path", {"path": "a.txt"})

    assert result.ok is True
    assert not (root / "a.txt").exists()
    moved = [item for item in trash.iterdir()]
    assert len(moved) == 1
    assert moved[0].read_text(encoding="utf-8") == "hello world\n"


@pytest.mark.asyncio
async def test_delete_path_trash_unavailable_keeps_original(
    ctx, registry, root, tmp_path, monkeypatch
):
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("occupied", encoding="utf-8")
    _patch_delete(monkeypatch, blocked)

    result = await _run(registry, ctx, "delete_path", {"path": "a.txt"})

    assert result.ok is False
    assert "trash_unavailable" in result.output
    assert (root / "a.txt").exists()


@pytest.mark.asyncio
async def test_delete_path_non_macos_is_unsupported(ctx, registry, monkeypatch):
    monkeypatch.setattr(
        "verilex_runtime.tools.files._IS_MACOS", False, raising=False
    )
    result = await _run(registry, ctx, "delete_path", {"path": "a.txt"})
    assert result.ok is False
    assert "trash_unsupported" in result.output


@pytest.mark.asyncio
async def test_copy_and_move_reject_existing_destination(ctx, registry, root):
    copy_result = await _run(
        registry,
        ctx,
        "copy_path",
        {"source": "a.txt", "destination": "sub/b.txt"},
    )
    assert copy_result.ok is False
    assert "target_exists" in copy_result.output
    assert (root / "sub" / "b.txt").read_text(encoding="utf-8") == "nested content\n"

    move_result = await _run(
        registry,
        ctx,
        "move_path",
        {"source": "a.txt", "destination": "sub/b.txt"},
    )
    assert move_result.ok is False
    assert "target_exists" in move_result.output
    assert (root / "a.txt").exists()


@pytest.mark.asyncio
async def test_copy_and_move_reject_out_of_bounds_sources_and_destinations(
    ctx, registry, root, outside
):
    (outside / "outside.txt").write_text("outside", encoding="utf-8")
    for name in ("copy_path", "move_path"):
        source_escape = await _run(
            registry,
            ctx,
            name,
            {"source": "../outside/outside.txt", "destination": "x.txt"},
        )
        assert source_escape.ok is False
        assert "path_out_of_bounds" in source_escape.output

        destination_escape = await _run(
            registry,
            ctx,
            name,
            {"source": "a.txt", "destination": "../outside/x.txt"},
        )
        assert destination_escape.ok is False
        assert "path_out_of_bounds" in destination_escape.output
    assert (outside / "outside.txt").exists()


@pytest.mark.asyncio
async def test_copy_rejects_symlinked_entries_in_tree(
    ctx, registry, root, outside
):
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (root / "linked-sub").mkdir()
    os.symlink(outside / "secret.txt", root / "linked-sub" / "link.txt")

    result = await _run(
        registry,
        ctx,
        "copy_path",
        {"source": "linked-sub", "destination": "linked-copy"},
    )

    assert result.ok is False
    assert "symlink_not_supported" in result.output
    assert not (root / "linked-copy").exists()


@pytest.mark.asyncio
async def test_copy_and_move_reject_symlink_source_or_destination(
    ctx, registry, root, outside
):
    (outside / "target.txt").write_text("t", encoding="utf-8")
    os.symlink(outside / "target.txt", root / "root-link")

    source_link = await _run(
        registry,
        ctx,
        "copy_path",
        {"source": "root-link", "destination": "copy.txt"},
    )
    assert source_link.ok is False
    assert "symlink_not_supported" in source_link.output

    destination_link = await _run(
        registry,
        ctx,
        "copy_path",
        {"source": "a.txt", "destination": "root-link"},
    )
    assert destination_link.ok is False
    assert "symlink_not_supported" in destination_link.output

    move_link = await _run(
        registry,
        ctx,
        "move_path",
        {"source": "root-link", "destination": "moved.txt"},
    )
    assert move_link.ok is False
    assert "symlink_not_supported" in move_link.output
    assert (root / "root-link").is_symlink()


@pytest.mark.asyncio
async def test_copy_move_delete_reject_missing_source(ctx, registry):
    for name, args in (
        ("copy_path", {"source": "missing.txt", "destination": "x.txt"}),
        ("move_path", {"source": "missing.txt", "destination": "x.txt"}),
        ("delete_path", {"path": "missing.txt"}),
    ):
        result = await _run(registry, ctx, name, args)
        assert result.ok is False
        assert "path_not_found" in result.output


@pytest.mark.asyncio
async def test_copy_move_delete_require_write_permission(root):
    read_only = ToolContext(root_path=root, allow_read=True, allow_write=False)
    registry_local = ToolRegistry()
    register_file_tools(registry_local)
    for name, args in (
        ("copy_path", {"source": "a.txt", "destination": "x.txt"}),
        ("move_path", {"source": "a.txt", "destination": "x.txt"}),
        ("delete_path", {"path": "a.txt"}),
    ):
        result = await registry_local.get(name).execute(read_only, args)
        assert result.ok is False
        assert "write_not_allowed" in result.output
    assert (root / "a.txt").exists()


def test_file_tools_registered_with_high_risk_and_write_scope(registry):
    for name in ("copy_path", "move_path", "delete_path"):
        definition = registry.get(name)
        assert definition is not None
        assert definition.risk_level is RiskLevel.HIGH
        assert definition.category is tool_registry.ToolCategory.FILES
        assert definition.scopes == frozenset({tool_registry.ToolScope.WRITE})
        assert definition.requires_workspace is True


# ---------------------------------------------------------------------------
# Word document reads (.docx / .doc) — harness-native extraction with no
# intermediate workspace files and no shell conversion by the model.


_CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_PACKAGE_RELS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def _write_docx(path, paragraphs):
    import zipfile

    body = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        archive.writestr("_rels/.rels", _PACKAGE_RELS_XML)
        archive.writestr("word/document.xml", document)
    return path


def _textutil_available():
    import shutil
    import sys

    return sys.platform == "darwin" and shutil.which("textutil") is not None


@pytest.mark.asyncio
async def test_read_file_extracts_docx_text(ctx, registry):
    _write_docx(
        ctx.root_path / "contract.docx",
        ["甲方：某某公司", "乙方：另一家公司"],
    )

    result = await _definition(registry, "read_file").execute(
        ctx, {"path": "contract.docx"}
    )

    assert result.ok is True
    assert "甲方：某某公司" in result.output
    assert "乙方：另一家公司" in result.output


@pytest.mark.asyncio
async def test_read_file_docx_leaves_workspace_untouched(ctx, registry):
    _write_docx(ctx.root_path / "contract.docx", ["正文"])

    before = sorted(p.name for p in ctx.root_path.iterdir())
    await _definition(registry, "read_file").execute(
        ctx, {"path": "contract.docx"}
    )
    after = sorted(p.name for p in ctx.root_path.iterdir())

    assert before == after


@pytest.mark.asyncio
async def test_read_file_invalid_docx_maps_to_stable_error(ctx, registry):
    (ctx.root_path / "broken.docx").write_bytes(b"PK\x03\x04 not a real package")

    with pytest.raises(ToolError) as captured:
        await _definition(registry, "read_file").execute(
            ctx, {"path": "broken.docx"}
        )

    assert captured.value.code == "file_not_readable"


@pytest.mark.asyncio
async def test_read_file_oversized_document_rejected_before_conversion(ctx, registry):
    from verilex_runtime.tools.files import MAX_READ_BYTES

    (ctx.root_path / "huge.docx").write_bytes(b"PK\x03\x04" + b"\0" * MAX_READ_BYTES)

    with pytest.raises(ToolError) as captured:
        await _definition(registry, "read_file").execute(
            ctx, {"path": "huge.docx"}
        )

    assert captured.value.code == "file_too_large"


@pytest.mark.skipif(not _textutil_available(), reason="textutil only on macOS")
@pytest.mark.asyncio
async def test_read_file_reads_legacy_doc_via_textutil(ctx, registry):
    import subprocess

    source = ctx.root_path / "_source.txt"
    source.write_text("旧版合同正文\n第二条", encoding="utf-8")
    subprocess.run(
        [
            "/usr/bin/textutil",
            "-convert",
            "doc",
            "-output",
            str(ctx.root_path / "legacy.doc"),
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    source.unlink()
    before = sorted(p.name for p in ctx.root_path.iterdir())

    result = await _definition(registry, "read_file").execute(
        ctx, {"path": "legacy.doc"}
    )

    assert result.ok is True
    assert "旧版合同正文" in result.output
    assert "第二条" in result.output
    assert sorted(p.name for p in ctx.root_path.iterdir()) == before


@pytest.mark.skipif(not _textutil_available(), reason="textutil only on macOS")
@pytest.mark.asyncio
async def test_read_file_garbage_doc_maps_to_stable_error(ctx, registry):
    (ctx.root_path / "garbage.doc").write_bytes(b"definitely not a doc file")

    with pytest.raises(ToolError) as captured:
        await _definition(registry, "read_file").execute(
            ctx, {"path": "garbage.doc"}
        )

    assert captured.value.code == "file_not_readable"


@pytest.mark.asyncio
async def test_read_file_doc_without_textutil_gives_actionable_error(ctx, registry):
    from verilex_runtime.tools import files as files_module

    (ctx.root_path / "legacy.doc").write_bytes(b"whatever")
    original = files_module._locate_textutil
    files_module._locate_textutil = lambda: None
    try:
        with pytest.raises(ToolError) as captured:
            await _definition(registry, "read_file").execute(
                ctx, {"path": "legacy.doc"}
            )
    finally:
        files_module._locate_textutil = original

    assert captured.value.code == "file_not_readable"
    assert ".docx" in captured.value.message


def test_read_file_description_mentions_word_formats(registry):
    definition = _definition(registry, "read_file")
    assert ".docx" in definition.description
    assert ".doc" in definition.description


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,args", [
    ("apply_file_patch", {"path":"sub/b.txt", "old_text":"nested", "new_text":"changed"}),
    ("move_path", {"source":"sub", "destination":"moved"}),
    ("delete_path", {"path":"sub", "recursive":True}),
])
async def test_bound_source_and_parent_cannot_be_mutated(ctx, root, registry, operation, args, monkeypatch, trash):
    from dataclasses import replace
    _patch_delete(monkeypatch, trash)
    protected = replace(ctx, protected_paths=(root / 'sub/b.txt',))
    try:
        result = await _run(registry, protected, operation, args)
    except ToolError as error:
        assert error.code == 'protected_source'
    else:
        assert not result.ok and 'protected_source' in result.output
    assert (root / 'sub/b.txt').read_text() == 'nested content\n'
    assert not (root / 'moved').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
async def test_bound_source_cannot_be_patched_through_alias(ctx, root, registry, link_kind):
    from dataclasses import replace
    alias = root / 'alias.txt'
    if link_kind == 'symlink': alias.symlink_to(root / 'sub/b.txt')
    else: os.link(root / 'sub/b.txt', alias)
    with pytest.raises(ToolError) as failure:
        await _run(registry, replace(ctx, protected_paths=(root/'sub/b.txt',)), 'apply_file_patch',
                   {'path':'alias.txt', 'old_text':'nested', 'new_text':'changed'})
    assert failure.value.code == 'protected_source'
    assert (root / 'sub/b.txt').read_text() == 'nested content\n'


@pytest.mark.asyncio
async def test_bound_source_allows_independent_new_output_and_copy(ctx, root, registry):
    from dataclasses import replace
    protected = replace(ctx, protected_paths=(root/'sub/b.txt',))
    assert (await _run(registry, protected, 'write_file', {'path':'new.txt','content':'new'})).ok
    assert (await _run(registry, protected, 'copy_path', {'source':'sub/b.txt','destination':'copy.txt'})).ok
    assert (root/'copy.txt').read_text() == 'nested content\n'


def test_missing_sandbox_does_not_claim_to_protect_bound_sources(tmp_path, monkeypatch):
    from verilex_runtime.tools import sandbox
    monkeypatch.setattr(sandbox, 'sandbox_available', lambda: False)
    with pytest.raises(ToolError) as failure:
        sandbox.build_sandbox_command(['echo','ok'], tmp_path, protected_paths=(tmp_path/'input.txt',))
    assert failure.value.code == 'sandbox_unavailable'
