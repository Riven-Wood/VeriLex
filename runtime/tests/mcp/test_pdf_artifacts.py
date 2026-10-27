import hashlib
import json
from types import SimpleNamespace as Obj

from verilex_runtime.pdf_plugin.artifacts import with_pdf_outputs
from verilex_runtime.storage.models import ToolCallStatus


def test_only_successful_bundled_outputs_in_current_workspace_are_previewable(tmp_path):
    relative = "PDF结果-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/扫描拆分页.pdf"
    data = {"outputs":[{"relative_path":relative,"path":str(tmp_path / relative)}]}
    content = {"content":[{"type":"text","text":json.dumps(data)}]}
    envelope = {"text":"⟦UNTRUSTED_MCP_RESULT_START⟧\n"+json.dumps(content)+"\n⟦UNTRUSTED_MCP_RESULT_END⟧","code":None,"truncated":False}
    stored = json.dumps({"ok":True,"output":json.dumps(envelope)})
    prefix = "mcp__"+hashlib.sha256(b"server").hexdigest()[:10]+"__"
    record = Obj(name=prefix+"pdf_split",result_json=stored,status=ToolCallStatus.SUCCEEDED,finished_at="now",started_at=None)
    tools = Obj(list_by_thread=lambda _: [record])
    server=Obj(id="server",transport="stdio",command="verilex-pdf-plugin",cwd=str(tmp_path),args=(),env={})
    repo=Obj(list_servers=lambda:[server])
    workspace=Obj(root_path=str(tmp_path),allow_read=True)
    workspaces=Obj(get=lambda _:workspace)
    thread=Obj(id="thread",workspace_id="workspace")
    result=with_pdf_outputs([],thread,tools,repo,workspaces)
    assert [r["path"] for r in result] == [relative]
    assert with_pdf_outputs(result,thread,tools,repo,workspaces) == result
    server.command="some-other-plugin"
    assert with_pdf_outputs([],thread,tools,repo,workspaces)==[]
    server.command="verilex-pdf-plugin"
    workspace.allow_read=False
    assert with_pdf_outputs([],thread,tools,repo,workspaces)==[]
    workspace.allow_read=True
    record.status=ToolCallStatus.FAILED
    assert with_pdf_outputs([],thread,tools,repo,workspaces)==[]
    record.status=ToolCallStatus.SUCCEEDED
    envelope["truncated"]=True
    record.result_json=json.dumps({"ok":True,"output":json.dumps(envelope)})
    assert with_pdf_outputs([],thread,tools,repo,workspaces)==[]
