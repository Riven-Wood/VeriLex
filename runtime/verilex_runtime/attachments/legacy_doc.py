"""Local legacy Word import. The office process receives only a private copy.

macOS currently uses an installed LibreOffice with an isolated, macro-disabled
profile and a network/write sandbox. Unsupported hosts fail explicitly.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile

from verilex_runtime.attachments.docx import MAX_DOCX_BYTES, inspect_docx
from verilex_runtime.attachments.errors import attachment_error

DOC_MEDIA_TYPE = "application/msword"
DOC_CONVERSION_NOTICE = "旧版 .doc 已在本机转换；复杂排版可能变化，导出统一为 .docx，原件未改动。"
_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")


def _sandbox_policy(root: Path) -> str:
    import json

    job = json.dumps(str(root))
    return ('(version 1)\n(allow default)\n(deny network*)\n'
            '(allow network* (local unix-socket))\n(deny file-write*)\n'
            '(deny file-read-data (subpath "/Users") (subpath "/Volumes") (subpath "/private/tmp"))\n'
            f'(allow file-read* file-write* (subpath {job}))\n'
            # A concrete operation deny overrides the wildcard file-read*
            # allow above. Explicitly allow data reads inside this job too.
            f'(allow file-read-data (subpath {job}))\n'
            '(allow file-read* (subpath "/private/var/db/timezone"))\n')


def convert_doc(data: bytes) -> bytes:
    if not data or len(data) > MAX_DOCX_BYTES:
        raise attachment_error("attachment_too_large" if data else "attachment_docx_invalid")
    if not data.startswith(_MAGIC):
        raise attachment_error("attachment_media_type_mismatch")
    office = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    if sys.platform != "darwin" or not office.is_file() or not Path("/usr/bin/sandbox-exec").is_file():
        raise attachment_error("attachment_doc_converter_missing")
    with tempfile.TemporaryDirectory(prefix="vld-", dir="/private/tmp") as raw:
        root = Path(raw).resolve()
        source = root / "source.doc"
        source.write_bytes(data)
        profile = root / "profile"
        (profile / "user").mkdir(parents=True)
        (profile / "user/registrymodifications.xcu").write_text('''<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>2</value></prop></item>
</oor:items>''')
        # Deny access to user files, other temp jobs, network, and all writes
        # outside this one job. System/application resources remain readable.
        sandbox = root / "import.sb"
        sandbox.write_text(_sandbox_policy(root))
        output = root / "source.docx"
        try:
            completed = subprocess.run([
                "/usr/bin/sandbox-exec", "-f", str(sandbox), str(office),
                f"-env:UserInstallation={profile.as_uri()}", f"-env:OSL_SOCKET_PATH={root}", "--headless", "--nologo",
                "--nodefault", "--norestore", "--infilter=MS Word 97", "--convert-to", "docx:Office Open XML Text",
                "--outdir", str(root), str(source),
            ], cwd=root, env={"PATH": "/usr/bin:/bin", "HOME": str(root), "CFFIXED_USER_HOME": str(root), "TMPDIR": str(root) + "/", "LANG": "en_US.UTF-8"},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
            if completed.returncode or not output.is_file() or output.is_symlink():
                raise attachment_error("attachment_doc_conversion_failed")
            if output.stat().st_size > MAX_DOCX_BYTES:
                raise attachment_error("attachment_too_large")
            # Reapply the OOXML trust boundary; converting does not imply safe.
            inspect_docx(output)
            return output.read_bytes()
        except (OSError, subprocess.TimeoutExpired):
            raise attachment_error("attachment_doc_conversion_failed") from None
