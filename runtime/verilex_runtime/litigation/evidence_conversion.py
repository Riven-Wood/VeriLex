"""Deterministic local conversion. Originals never enter a writable Office process."""
from pathlib import Path
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pypdf import PdfReader
from .service import _failure

SUPPORTED = {'.pdf','.png','.jpg','.jpeg','.doc','.docx','.odt','.rtf','.xlsx','.xls','.ods','.ppt','.pptx','.odp','.txt','.md','.csv','.json'}
MAX_BYTES = 100 * 1024 * 1024


def pdf_pages(data):
    try:
        reader=PdfReader(io.BytesIO(data))
        if reader.is_encrypted or not 0<len(reader.pages)<=1000:raise ValueError()
        return len(reader.pages)
    except Exception:
        raise _failure('litigation_source_invalid','PDF 无法解析、已加密或超过 1000 页。') from None


def plugin_job(job, inputs):
    from .. import pdf_plugin
    assets=Path(pdf_plugin.__file__).parent
    node=assets.parent/'knowledge/ima_mcp/node' if getattr(sys,'frozen',False) else shutil.which('node')
    script=assets/'evidence.cjs'
    if not node or not Path(node).is_file() or not script.is_file():
        raise _failure('litigation_converter_missing','PDF 处理组件缺失，请重新安装完整应用。')
    with tempfile.TemporaryDirectory(prefix='verilex-evidence-') as raw:
        root=Path(raw)
        for name,data in inputs.items():(root/name).write_bytes(data)
        try:
            result=subprocess.run([str(node),'--max-old-space-size=512',str(script)],input=json.dumps(job).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=root,timeout=120)
            output=root/'result.pdf'
            if result.returncode or not output.is_file() or output.stat().st_size>MAX_BYTES:raise ValueError()
            data=output.read_bytes();pdf_pages(data);return data
        except (OSError,ValueError,subprocess.TimeoutExpired):
            raise _failure('litigation_conversion_failed','PDF 处理失败；未生成缺件的证据册。') from None


def convert(data,suffix,*,font_files=()):
    if suffix=='.pdf':pdf_pages(data);return data
    if suffix in {'.png','.jpg','.jpeg'}:
        name='source'+suffix
        return plugin_job({'operation':'image','input':name},{name:data})
    if suffix not in SUPPORTED:raise _failure('litigation_conversion_required','此格式暂不支持转换，请先另存为 PDF。')
    from ..attachments.legacy_doc import _sandbox_policy
    office=Path('/Applications/LibreOffice.app/Contents/MacOS/soffice')
    if sys.platform!='darwin' or not office.is_file():
        raise _failure('litigation_converter_missing','转换 Office 文件需要本机安装 LibreOffice；也可先另存为 PDF。')
    with tempfile.TemporaryDirectory(prefix='vle-',dir='/private/tmp') as raw:
        root=Path(raw).resolve();profile=root/'profile';(profile/'user').mkdir(parents=True)
        (profile/'user/registrymodifications.xcu').write_text('''<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item><item oor:path="/org.openoffice.Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>2</value></prop></item></oor:items>''')
        if suffix in {'.md','.json','.csv'}:suffix='.txt'
        source=root/('source'+suffix);source.write_bytes(data)
        policy=root/'convert.sb'
        # Only callers supplying a known installed font may extend read access;
        # no source document, user directory, or network permission is added.
        font_rules=[]
        for font in font_files:
            font=Path(font)
            if font.is_symlink() or font.suffix.lower() not in {'.ttf','.ttc','.otf'} or not font.is_file():
                raise _failure('litigation_font_missing','质证文书字体不可读取，请检查本机字体安装。')
            font_rules.append('(allow file-read-data (literal '+json.dumps(str(font.resolve()))+'))\n')
        policy.write_text(_sandbox_policy(root)+''.join(font_rules))
        try:
            result=subprocess.run(['/usr/bin/sandbox-exec','-f',str(policy),str(office),f'-env:UserInstallation={profile.as_uri()}',f'-env:OSL_SOCKET_PATH={root}','--headless','--nologo','--nodefault','--norestore','--convert-to','pdf','--outdir',str(root),str(source)],cwd=root,env={'PATH':'/usr/bin:/bin','HOME':str(root),'CFFIXED_USER_HOME':str(root),'TMPDIR':str(root)+'/', 'LANG':'en_US.UTF-8'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=90)
            output=root/'source.pdf'
            if result.returncode or not output.is_file() or output.is_symlink() or output.stat().st_size>MAX_BYTES:raise ValueError()
            converted=output.read_bytes();pdf_pages(converted);return converted
        except (OSError,ValueError,subprocess.TimeoutExpired):
            raise _failure('litigation_conversion_failed','本机转换失败，请用原应用检查文件或另存为 PDF 后重试。') from None
