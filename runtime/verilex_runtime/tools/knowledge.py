"""Read-only model tools; authorization comes solely from server turn scopes."""
import asyncio
import json
from verilex_runtime.knowledge.turn_sources import TurnSources
from verilex_runtime.storage.models import RiskLevel
from verilex_runtime.tools.registry import ToolDefinition, ToolError, ToolCategory, ToolScope, ToolResult

NAMES = frozenset({'knowledge_search', 'knowledge_read', 'knowledge_quote', 'knowledge_follow_link', 'knowledge_find'})


def register_knowledge_tools(registry, sources: TurnSources):
    schemas = {
        'knowledge_find': ('在本轮已找到的本地资料中定位关键词的多处出现，每页最多20处。目录提及不够时继续找正文；结果不是已读引用。用返回的read_arguments读取，next_start继续查找。', {
            'candidate_id': {'type':'string'}, 'query': {'type':'string','minLength':1,'maxLength':256},
            'start': {'type':'integer','minimum':0}}, ['candidate_id','query']),
        'knowledge_follow_link': ('按已读原文返回的链接index定位目标候选，不自动读取或联网。重名须明确目标，嵌入引文归目标原文。', {
            'receipt_id': {'type':'string'}, 'link_index': {'type':'integer','minimum':0,'maximum':49}}, ['receipt_id','link_index']),
        'knowledge_search': ('搜索本轮已选知识库。本地库按连续词句匹配；ima通过官方search_knowledge接口检索全库。ima返回search_excerpt时可直接据片段分析并注明来源，无需下载全文；metadata_only仅是资料线索。本地候选须读取后分析。一次一个短关键词，可换词或翻页。', {
            'library_id': {'type':'string','description':'只选一个库时省略此参数；多库时必须原样使用本轮来源目录id，不是name。'}, 'query': {'type':'string','maxLength':256},
            'offset': {'type':'integer','minimum':0,'maximum':20000}}, ['query']),
        'knowledge_read': ('读取本轮候选原文。省略start会直接读取搜索命中附近；可用搜索或find返回的read_arguments。next_read/previous_read继续上下文。位置按Unicode码点，从0开始；保留标点、换行。引用须用返回的receipt_id。', {
            'candidate_id': {'type':'string'}, 'start': {'type':'integer','minimum':0},
            'max_chars': {'type':'integer','minimum':1,'maximum':10000}}, ['candidate_id']),
        'knowledge_quote': ('仅为直接回答用户问题所需的已读片段生成原文卡。扩词命中或邻近主题不是出卡理由；已有直接资料时不追加仅背景相关的资料，除非用户要求比较或扩展。选择本次read的quote_options片段生成原文卡。正文优先选complete_line；p0仅代表整个已读窗口。只提交receipt_id和passage_id，不手算字符位置、不复写正文。', {
            'receipt_id': {'type':'string'}, 'passage_id': {'type':'string'}, 'end_passage_id': {'type':'string','description':'可选，同次读取的最后一个完整行编号，程序引用两编号之间全部连续原文。'}}, ['receipt_id','passage_id']),
    }
    for name, (description, properties, required) in schemas.items():
        def make_execute(tool_name):
            async def execute(context, arguments):
                if not context.thread_id or not context.turn_id:
                    raise ToolError('knowledge_scope_unavailable', '本轮未选择知识库。')
                operation = getattr(sources, tool_name.removeprefix('knowledge_'))
                value = await asyncio.to_thread(operation, context.thread_id, context.turn_id, **arguments)
                if tool_name == 'knowledge_quote':
                    # Cards load the immutable text from the historical-quote API.
                    # Return a save acknowledgement, not a second copy for the model
                    # to transcribe. Read receipts and their original text stay intact.
                    value = {key: value[key] for key in (
                        'quote_id', 'source_name', 'line_start', 'line_end')}
                    value['display_notice'] = (
                        '原文卡已显示，文字与位置由程序提供。接下来只写分析结论及资料不足之处；'
                        '不要重抄、缩写或用省略号改述原句，不描述原句的标点或逐项复述卡片。'
                        '要查看原文，请用户点击对应文件的原文卡。')
                return ToolResult(ok=True, output=json.dumps(value, ensure_ascii=False))
            return execute
        registry.register(ToolDefinition(name=name, description=description,
            parameters={'type':'object','properties':properties,'required':required,'additionalProperties':False},
            risk_level=RiskLevel.LOW, execute=make_execute(name), category=ToolCategory.OTHER,
            scopes=frozenset({ToolScope.READ}), requires_workspace=False))
