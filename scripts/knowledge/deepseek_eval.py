"""Explicitly authorized live evaluation; synthetic corpus, isolated history.

Uses ProviderService for credentials without exposing/copying their values.
Only a configured api.deepseek.com provider is eligible. HTTP transport enforces
60 requests and a conservative USD1 reservation budget (10 CNY/USD allowance).
No remote MCP or actual user corpus is loaded. Output is synthetic evidence only.
"""
import asyncio
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from time import perf_counter
from urllib.parse import urlsplit
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'runtime'))
from baseline import evaluation_corpus
from scoring import score
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.providers.openai_compatible import OpenAICompatibleChatStream
from verilex_runtime.providers.probe_router import ProviderProbeRouter
from verilex_runtime.secrets.local_store import LocalSecretStore
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.models import ProviderRecord,ProviderKind,AuthMode
from verilex_runtime.storage.thread_repository import ThreadRepository,TurnRepository,MessageRepository,EventRepository
from verilex_runtime.storage.tool_repository import ToolCallRepository
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.knowledge.service import KnowledgeService
from verilex_runtime.knowledge.turn_sources import TurnSources
from verilex_runtime.tools import ToolRegistry
from verilex_runtime.tools.knowledge import register_knowledge_tools

class BudgetTransport(httpx.AsyncBaseTransport):
    def __init__(self, ledger, max_requests=60, max_usd=1.0):
        if type(max_requests) is not int or not 1 <= max_requests <= 60 or not 0 < max_usd <= 1.0:
            raise ValueError("invalid_evaluation_budget")
        self.max_requests, self.max_usd = max_requests, max_usd
        self.blocked_reason = None
        self.ledger=ledger
        self.inner=httpx.AsyncHTTPTransport(retries=0)
        saved=json.loads(ledger.read_text()) if ledger.exists() else {}
        self.calls=saved.get('requests',0);self.reserved_usd=saved.get('reserved_usd',0.);self.statuses=[]
    async def handle_async_request(self, request):
        if request.url.scheme!='https' or request.url.host!='api.deepseek.com' or not request.url.path.endswith('/chat/completions'):
            raise RuntimeError('evaluation_endpoint_rejected')
        data=json.loads(request.content)
        data['max_tokens']=1536
        data['thinking']={'type':'disabled'}
        raw=json.dumps(data,ensure_ascii=False).encode()
        # Verified Flash peak prices, no cache credit, large input upper
        # allowance. This ledger includes interrupted/failed request reservations.
        reserve=(len(raw)*2+4096)*.3/1_000_000+1536*1.2/1_000_000
        if self.calls>=self.max_requests or self.reserved_usd+reserve>self.max_usd:
            self.blocked_reason = 'evaluation_budget_exhausted'
            raise RuntimeError('evaluation_budget_exhausted')
        self.calls+=1;self.reserved_usd+=reserve
        self.ledger.write_text(json.dumps({"requests":self.calls,"reserved_usd":self.reserved_usd}))
        headers=dict(request.headers);headers.pop('content-length',None)
        amended=httpx.Request(request.method,request.url,headers=headers,content=raw,extensions=request.extensions)
        response=await self.inner.handle_async_request(amended)
        self.statuses.append(response.status_code)
        return response
    async def aclose(self): await self.inner.aclose()

async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('provider_id')
    parser.add_argument('output_directory', type=Path)
    parser.add_argument('budget_ledger', type=Path)
    parser.add_argument('--max-requests', type=int, default=60)
    parser.add_argument('--max-usd', type=float, default=1.0)
    parser.add_argument('--suite', choices=['v1', 'v2', 'long'], default='v1')
    parser.add_argument('--questions', nargs='+', default=None)
    args=parser.parse_args()
    if args.suite == 'long':
        from long_fixture import corpus as long_corpus
        sources, questions = long_corpus()
    else:
        sources,questions=evaluation_corpus(args.suite)
    if args.questions is None:
        prefix = 'v2-' if args.suite == 'v2' else ''
        args.questions = [q['id'] for q in questions] if args.suite == 'long' else [prefix + qid for qid in ['exact-0','natural-0','absent-6','multi-6','natural-10','natural-12']]
    if set(args.questions)-{q['id'] for q in questions}:
        parser.error('Unknown synthetic question ID')
    provider_id,out=args.provider_id,args.output_directory;out.mkdir(exist_ok=False)
    state=Path.home()/'.config/verilex'
    actual_repo=ProviderRepository(Database(state/'verilex.sqlite3'))
    configured=actual_repo.get(provider_id)
    if configured is None or urlsplit(configured.base_url).hostname!='api.deepseek.com' or configured.default_model not in ('deepseek-flash','deepseek-v4-flash'):
        raise SystemExit('Configured DeepSeek provider required')
    budget=BudgetTransport(args.budget_ledger, args.max_requests, args.max_usd)
    async with httpx.AsyncClient(transport=budget) as client:
        providers=ProviderService(actual_repo,LocalSecretStore(state/'secrets.json'),ProviderProbeRouter(None,None),
            chat_stream_factory=lambda record,key,model: OpenAICompatibleChatStream(client,record.base_url,key,model))
        db=Database(out/'evaluation.sqlite3');run_migrations(db)
        fake_repo=ProviderRepository(db)
        fake_repo.create(ProviderRecord('eval','DeepSeek evaluation',ProviderKind.OPENAI_COMPATIBLE,'https://api.deepseek.com',configured.default_model,AuthMode.NONE,None,True,'now','now'))
        threads=ThreadService(ThreadRepository(db),fake_repo)
        turns=TurnService(ThreadRepository(db),TurnRepository(db),MessageRepository(db),EventRepository(db))
        service=KnowledgeService(db);libs={}
        for lib in ('selected','unselected'):
            root=out/lib;root.mkdir()
            for src in sources:
                if src.library_id==lib:(root/src.source_name).write_bytes(src.raw)
            libs[lib]=service.associate(str(root),lib);service.sync(libs[lib])
        broker=TurnSources(service);registry=ToolRegistry();register_knowledge_tools(registry,broker)
        usages=[]
        model_attempts=[]
        class Metered:
            async def stream(self,messages,tools=None,options=None):
                attempt = {'phase': 'main', 'text': '', 'tool_calls': []}
                model_attempts.append(attempt)
                async for delta in providers.chat_stream_for(provider_id,configured.default_model).stream(messages,tools,options):
                    if delta.kind=='usage':usages.append(asdict(delta.usage))
                    elif delta.kind=='text': attempt['text'] += delta.text
                    elif delta.kind=='tool_call' and delta.tool_call is not None: attempt['tool_calls'].append(asdict(delta.tool_call))
                    yield delta
        engine=AgentEngine(threads,turns,lambda *_:Metered(),tool_registry=registry,
            tool_calls=ToolCallRepository(db),knowledge_sources=broker)
        ids=args.questions
        results=[]
        for qid in ids:
            if budget.calls>=budget.max_requests or budget.reserved_usd>=budget.max_usd:break
            q=next(q for q in questions if q['id']==qid)
            t=threads.create(qid,configured.default_model,provider_id='eval')
            prompt=q['query']+'。请只查本轮已选库，可换关键词继续寻找。找到相关资料后用原文卡引用；没有相关资料就说明检索不足，勿编造。资料均为虚构测试材料，不需要真实法律分析。'
            if q['category']=='multi': prompt+='请分别查阅这两个主题的资料后对比。'
            turn=engine.start(t.id,prompt,knowledge_library_ids=[libs['selected']])
            before=budget.calls;attempt_start=len(model_attempts);started=perf_counter()
            async def emit(_): pass
            try:result=await asyncio.wait_for(engine.run(turn.id,emit),timeout=150)
            except asyncio.TimeoutError:
                engine.cancel(turn.id);results.append({'id':qid,'status':'timeout'});break
            calls=ToolCallRepository(db).list(turn.id)
            with db.connect() as c:
                quotes=[json.loads(r[0]) for r in c.execute('SELECT payload FROM knowledge_quotes WHERE turn_id=?',(turn.id,))]
            exact=True
            for quote in quotes:
                original=(out/'selected'/quote['source_name']).read_bytes().decode('utf8')
                exact &= quote['text']==original[quote['start']:quote['end']]
            quoted={Path(q['source_name']).stem for q in quotes}
            relevant=set(q['relevant'])
            record={'id':qid,'category':q['category'],'split':q['split'],'question':q['query'],
                'status':result.status.value,'error_code':result.error_code,'evaluation_stop_reason':budget.blocked_reason,'requests':budget.calls-before,
                'seconds':round(perf_counter()-started,2),'quoted_documents':sorted(quoted),
                'relevant':sorted(relevant),'relevant_quote':bool(quoted & relevant),
                'all_required_quoted':relevant.issubset(quoted),'quotes_exact':exact if quotes else None,'quotes':quotes,
                'tools':[{'name':x.name,'status':x.status.value,'arguments':x.arguments_json,'result':x.result_json} for x in calls],
                'messages':[asdict(m) for m in MessageRepository(db).list(t.id)],
                'model_attempts': model_attempts[attempt_start:]}
            record['evidence_score'] = score(q, quotes, record['tools'], sources, result.status.value)
            if q.get('required_passage'):
                record['required_passage_quoted'] = any(q['required_passage'] in quote['text'] for quote in quotes)
            results.append(record)
            report={'suite':args.suite,'model':configured.default_model,'requests':budget.calls,'reserved_usd_upper_allowance':budget.reserved_usd,
                    'http_statuses':budget.statuses,'usage':usages,'results':results}
            (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps({k:record[k] for k in ('id','status','requests','seconds','quoted_documents','quotes_exact')},ensure_ascii=False),flush=True)
            if result.status.value=='failed':break
        print('report='+str(out/'report.json'),flush=True)
if __name__=='__main__':
    try:asyncio.run(main())
    except KeyboardInterrupt: sys.exit(130)
    except Exception as error:
        print('Evaluation stopped: '+type(error).__name__,file=sys.stderr);sys.exit(1)
