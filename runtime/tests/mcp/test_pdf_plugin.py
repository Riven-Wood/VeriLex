"""Real supervised stdio MCP, synthetic PDFs, no provider/network/account."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest
from pypdf import PdfReader, PdfWriter

from verilex_runtime.mcp.client import MCPResolvedServer, _connect_sdk
from verilex_runtime.mcp.importer import MCPServerDraft
from verilex_runtime.mcp.schema import validate_mcp_input_schema
from verilex_runtime.pdf_plugin.launcher import resolve_command
from verilex_runtime.mcp.errors import MCPDomainError


def test_real_pdf_mcp_pipeline(tmp_path):
    writer = PdfWriter()
    for width in (201, 202, 203):
        writer.add_blank_page(width=width, height=400)
    writer.write(tmp_path / "甲.pdf")
    writer = PdfWriter()
    writer.add_blank_page(width=501, height=600)
    writer.write(tmp_path / "乙.pdf")
    writer.encrypt("synthetic-password")
    writer.write(tmp_path / "加密.pdf")
    (tmp_path / "损坏.pdf").write_bytes(b"%PDF- broken")
    originals = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    script = Path(os.environ.get("VERILEX_PDF_TEST_SCRIPT", Path(__file__).parents[2] / "verilex_runtime/pdf_plugin/server.cjs"))
    assert script.is_file(), "build the PDF plugin before its real MCP test"
    draft = MCPServerDraft(display_name="PDF synthetic", transport="stdio", command=shutil.which("node") if "VERILEX_PDF_TEST_SCRIPT" in os.environ else "verilex-pdf-plugin",
                           args=(str(script),) if "VERILEX_PDF_TEST_SCRIPT" in os.environ else (), cwd=str(tmp_path), request_timeout_ms=10000)

    async def run():
        connection = await _connect_sdk(MCPResolvedServer("pdf-synthetic", draft, 0))
        try:
            discovered, cursor = await connection.list_tools_page(None)
            assert cursor is None
            assert {t.name for t in discovered} == {"pdf_merge", "pdf_split", "pdf_extract", "pdf_page_count"}
            for tool in discovered:
                validate_mcp_input_schema(tool.inputSchema)

            async def call(name, args):
                result = await connection.call_tool(name, args)
                assert not result.isError, result
                return json.loads(result.content[0].text)

            result = await call("pdf_merge", {"inputs": ["乙.pdf", "甲.pdf"], "output_name": "合并.pdf"})
            output = result["outputs"][0]
            assert [p.mediabox.width for p in PdfReader(output["path"]).pages] == [501, 201, 202, 203]
            assert output["pages"] == 4
            split = await call("pdf_split", {"input": "甲.pdf", "ranges": "1-2,3", "output_name": "拆分.pdf"})
            assert [len(PdfReader(o["path"]).pages) for o in split["outputs"]] == [2, 1]
            extracted = await call("pdf_extract", {"input": "甲.pdf", "pages": "3,1", "output_name": "甲.pdf"})
            assert [p.mediabox.width for p in PdfReader(extracted["outputs"][0]["path"]).pages] == [203, 201]
            before = set(tmp_path.iterdir())
            for name, args, message in [
                ("pdf_extract", {"input":"甲.pdf", "pages":"4", "output_name":"坏结果.pdf"}, "共 3 页"),
                ("pdf_page_count", {"input":"加密.pdf"}, "已加密"),
                ("pdf_page_count", {"input":"损坏.pdf"}, "处理失败"),
                ("pdf_page_count", {"input":"没有.pdf"}, "没有找到"),
                ("pdf_page_count", {"input":"../甲.pdf"}, "选定的文件夹"),
            ]:
                failed = await connection.call_tool(name, args)
                assert failed.isError
                assert message in failed.content[0].text
            assert set(tmp_path.iterdir()) == before
        finally:
            await connection.aclose()

    asyncio.run(run())
    for name, digest in originals.items():
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == digest


def test_pdf_launcher_rejects_extra_arguments_or_environment(tmp_path):
    for patch in ({"args": ("--eval", "x")}, {"env": {"NODE_OPTIONS": "x"}}, {"cwd": None}):
        fields = {"display_name":"PDF", "transport":"stdio", "command":"verilex-pdf-plugin", "cwd":str(tmp_path)}
        fields.update(patch)
        with pytest.raises(MCPDomainError):
            resolve_command(MCPServerDraft(**fields))
