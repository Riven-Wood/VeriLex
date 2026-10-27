"""Synthetic local-source capacity probe. No network, provider, or real documents."""
import json
from pathlib import Path
import tempfile
from time import perf_counter
from verilex_runtime.knowledge.service import KnowledgeService
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations


def probe(count):
    with tempfile.TemporaryDirectory(prefix='verilex-knowledge-capacity-') as directory:
        base=Path(directory); root=base/'sources'; root.mkdir()
        for i in range(count):
            (root/f'{i:05d}.md').write_text(f'# 合成资料 {i}\n唯一编号{i:05d}\n'+('测试正文，保留标点；不是真实法律资料。\n'*20))
        db=Database(base/'index.sqlite3'); run_migrations(db)
        service=KnowledgeService(db); identifier=service.associate(str(root))
        start=perf_counter(); service.sync(identifier); initial=perf_counter()-start
        start=perf_counter(); service.sync(identifier); unchanged=perf_counter()-start
        start=perf_counter(); matches=service.documents(identifier,'唯一编号00000')['items']; search=perf_counter()-start
        state=service.libraries()[0]
        assert len(matches)==1
        assert state['status']==('ready' if count<=service.MAX_DOCUMENTS else 'partial')
        return {'files':count,'indexed':state['count'],'status':state['status'],'initial_seconds':round(initial,3),'unchanged_seconds':round(unchanged,3),'search_ms':round(search*1000,2),'database_bytes':db.path.stat().st_size}

if __name__=='__main__':
    print(json.dumps([probe(count) for count in (100,1000,5000)],ensure_ascii=False,indent=2))
