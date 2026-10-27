import json
from types import SimpleNamespace as S
from verilex_runtime.excel_plugin.artifacts import with_excel_outputs
from verilex_runtime.mcp.tools import stable_mcp_tool_name
from verilex_runtime.storage.models import ToolCallStatus


def test_only_successful_bundled_outputs_in_current_folder_are_listed(tmp_path):
    server = S(id='excel', display_name='Excel', command='verilex-excel-plugin', transport='stdio', cwd=str(tmp_path), args=[], env={})
    result = {'created_file': str(tmp_path/'成果.xlsx'), 'source_preserved': True}
    record = S(name=stable_mcp_tool_name('excel', 'Excel', 'excel_deliver_workbook'), status=ToolCallStatus.SUCCEEDED,
        result_json=json.dumps({'ok':True,'output':'⟦UNTRUSTED_MCP_RESULT_START⟧\n'+json.dumps({'content':[{'type':'text','text':json.dumps(result)}]})+'\n⟦UNTRUSTED_MCP_RESULT_END⟧'}),
        arguments_json=json.dumps({'output':'成果.xlsx'}),finished_at='2026-10-12',started_at='2026-10-12')
    thread=S(id='t', workspace_id='w'); repo=S(list_servers=lambda:[server]); calls=S(list_by_thread=lambda _: [record]); ws=S(get=lambda _:S(root_path=str(tmp_path),allow_read=True))
    from verilex_runtime.mcp.results import sanitize_mcp_result
    envelope = sanitize_mcp_result({'content':[{'type':'text','text':json.dumps(result)}], 'isError':False})
    record.result_json = json.dumps({'ok': True, 'output': json.dumps(envelope.as_dict())})
    output=with_excel_outputs([],thread,calls,repo,ws)
    assert output[0]['path']=='成果.xlsx'
    assert with_excel_outputs(output,thread,calls,repo,ws)==output
    server.command='untrusted-external-server'
    assert with_excel_outputs([],thread,calls,repo,ws)==[]
    server.command='verilex-excel-plugin'; record.status=ToolCallStatus.FAILED
    assert with_excel_outputs([],thread,calls,repo,ws)==[]
    record.status=ToolCallStatus.SUCCEEDED; record.arguments_json='{"output":"different.xlsx"}'
    assert with_excel_outputs([],thread,calls,repo,ws)==[]
