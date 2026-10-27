"""Register the bounded, bundled open-source PowerPoint plugin with local tools."""
import json
from verilex_runtime.storage.models import RiskLevel
from verilex_runtime.tools.registry import ToolDefinition, ToolCategory, ToolScope, ToolResult
from .service import PowerPointPlugin, SLIDES


def register_ppt_plugin(registry, publisher, *, now):
    plugin = PowerPointPlugin(publisher, now=now)
    path = {'type':'string','maxLength':1024,'description':'工作文件夹内现有 PPTX 的相对路径；不接受附件ID或外部路径'}
    filename = {'type':'string','maxLength':120,'description':'新成果文件名，以 .pptx 结尾；同名自动另存，原件保留'}
    definitions = [
        ('ppt_plugin_read', '开源 PowerPoint 插件：读取工作区 PPTX 的普通文字、表格和备注，不还原图片或版式。仅支持1至100页、25MB内的.pptx。', {'path':path}, ['path'], False),
        ('ppt_plugin_create', '开源 PowerPoint 插件：按逐页标题、要点和备注在本机生成可编辑 .pptx。1至40页，每页最多6条要点。成果进入文件列表。', {'output_name':filename,'slides':SLIDES}, ['output_name','slides'], True),
        ('ppt_plugin_replace', '开源 PowerPoint 插件：替换工作区 PPTX 同一格式片段内的文字并交付新副本。保留原件；不支持跨格式片段、组合形状、图表或SmartArt文字。新文字可能影响排版。', {'path':path,'output_name':filename,'old_text':{'type':'string','minLength':1,'maxLength':500},'new_text':{'type':'string','maxLength':500}}, ['path','output_name','old_text','new_text'], True),
    ]
    for name, description, properties, required, write in definitions:
        async def execute(context, args, operation=name):
            result = plugin.read(context,args) if operation == 'ppt_plugin_read' else plugin.write(context,args,edit=operation=='ppt_plugin_replace')
            return ToolResult(ok=True, output=json.dumps(result, ensure_ascii=False))
        registry.register(ToolDefinition(name=name, description=description,
            parameters={'type':'object','properties':properties,'required':required,'additionalProperties':False},
            risk_level=RiskLevel.HIGH if write else RiskLevel.LOW, execute=execute,
            category=ToolCategory.DOCUMENTS, source_name='PowerPoint 开源插件（MIT）',
            scopes=frozenset({ToolScope.READ,ToolScope.WRITE} if write else {ToolScope.READ}), requires_workspace=True))
