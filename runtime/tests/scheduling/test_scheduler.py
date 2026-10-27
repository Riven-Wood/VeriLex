import asyncio
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pytest
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.thread_repository import ThreadRepository
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.scheduling.models import TaskInput, Schedule, compute_next_run, stamp
from verilex_runtime.scheduling.store import ScheduleStore
from verilex_runtime.scheduling.scheduler import SchedulerService
from verilex_runtime.scheduling.errors import ScheduleDomainError


class FakeEngine:
    def __init__(self):
        self.fail = False
        self.wait = False
        self.calls = []
    def start(self, *args, **kwargs):
        self.calls.append((args,kwargs))
        return SimpleNamespace(id='turn')
    async def run(self, *args):
        if self.wait:
            await asyncio.Event().wait()
        return SimpleNamespace(status=SimpleNamespace(value='failed' if self.fail else 'completed'), error_code='turn_limit_exceeded' if self.fail else None)


@pytest.fixture
def env(tmp_path):
    db=Database(tmp_path/'test.db');run_migrations(db)
    with db.connect() as c:
        c.execute("INSERT INTO providers(id,name,kind,base_url,default_model,auth_mode,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", ("p","test","openai_compatible","https://example.test/v1","m","none",1,"2026-09-17T00:00:00Z","2026-09-17T00:00:00Z"))
    now=[datetime(2026,9,17,0,tzinfo=timezone.utc)]
    engine=FakeEngine()
    service=SchedulerService(ScheduleStore(db),ThreadService(ThreadRepository(db)),engine,now=lambda:now[0],zone=ZoneInfo('Asia/Shanghai'))
    return service,now,engine


def value(**changes):
    return TaskInput(**({'name':'日报','instruction':'整理摘要','providerId':'p','model':'m','schedule':{'kind':'daily','timeOfDay':'09:00'}}|changes))


def test_times():
    now=datetime(2026,9,17,0,tzinfo=timezone.utc);zone=ZoneInfo('Asia/Shanghai')
    assert compute_next_run(Schedule(kind='daily',timeOfDay='09:00'),now=now,zone=zone)== '2026-09-17T01:00:00.000000Z'
    assert compute_next_run(Schedule(kind='weekly',weekday=0,timeOfDay='09:00'),now=now,zone=zone)== '2026-09-21T01:00:00.000000Z'
    assert compute_next_run(Schedule(kind='once',fireAt='2026-09-18T01:00:00Z'),now=now,zone=zone,run_count=1) is None
    assert compute_next_run(Schedule(kind='daily',timeOfDay='09:00'),now=now,zone=zone,enabled=False) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('original_kind', ['daily', 'once'])
async def test_reschedule_once_preserves_history_and_runs_new_date(env, original_kind):
    s, clock, engine = env
    original = ({'kind': 'daily', 'timeOfDay': '09:00'} if original_kind == 'daily'
                else {'kind': 'once', 'fireAt': stamp(clock[0] + timedelta(hours=1))})
    task = s.save(value(schedule=original))
    s.run_now(task['id']); await asyncio.gather(*s._running.values())
    fire_at = stamp(clock[0] + timedelta(days=1))
    updated = s.save(value(schedule={'kind': 'once', 'fireAt': fire_at}), task['id'])
    assert updated['nextRun'] == fire_at
    assert updated['runCount'] == 1 and updated['scheduleRunCount'] == 0
    assert len(s.store.runs(task['id'])) == 1
    # Reopen from stored JSON, including an ordinary instruction edit.
    updated = s.save(value(name='改名', schedule={'kind': 'once', 'fireAt': fire_at}), task['id'])
    assert updated['scheduleRunCount'] == 0
    clock[0] += timedelta(days=1, minutes=1)
    await s._tick(); await asyncio.gather(*s._running.values())
    latest = s.store.get(task['id'])
    assert latest['runCount'] == 2 and latest['scheduleRunCount'] == 1
    assert latest['nextRun'] is None
    await s._tick(); assert not s._running
    assert len(s.store.runs(task['id'])) == 2
    with pytest.raises(ScheduleDomainError): s.run_now(task['id'])


def test_old_completed_once_json_is_not_rearmed(env):
    s, clock, _ = env
    task = s.save(value(schedule={'kind':'once','fireAt':stamp(clock[0] + timedelta(days=1))}))
    task.pop('scheduleRunCount'); task['runCount'] = 1
    s.store.save(task)
    assert s.set_enabled(task['id'], True)['nextRun'] is None
    with pytest.raises(ScheduleDomainError): s.run_now(task['id'])


@pytest.mark.asyncio
async def test_catchup_coalesces_and_persists_success(env):
    s,clock,engine=env;t=s.save(value());clock[0]+=timedelta(days=3)
    s._started_at=stamp(clock[0]);await s._tick();await asyncio.gather(*s._running.values())
    row=s.store.get(t['id']);runs=s.store.runs(t['id'])
    assert row['runCount']==1 and row['lastStatus']=='succeeded'
    assert runs[0]['triggerKind']=='catchup' and runs[0]['threadId']
    assert row['nextRun']>stamp(clock[0])
    await s._tick();assert not s._running
    assert engine.calls[0][1]['approval_mode']=='ask'
    assert engine.calls[0][1]['wall_clock_seconds']==900


@pytest.mark.asyncio
async def test_overlap_global_cap_disable_and_delete(env):
    s,clock,engine=env;engine.wait=True
    one=s.save(value());two=s.save(value(name='第二个'))
    s.run_now(one['id']);await asyncio.sleep(0)
    for action in (lambda:s.run_now(one['id']),lambda:s.run_now(two['id']),lambda:s.delete(one['id']),lambda:s.save(value(),one['id'])):
        with pytest.raises(ScheduleDomainError) as e:action()
        assert e.value.http_status==409
    s.set_enabled(one['id'],False)
    await s.close()
    assert s.store.runs(one['id'])[0]['status']=='interrupted'
    assert s.store.get(one['id'])['nextRun'] is None
    s._closing=False;s.delete(one['id']);assert len(s.store.list())==1


@pytest.mark.asyncio
async def test_failure_once_no_retry_and_history_retention(env):
    s,clock,engine=env;engine.fail=True
    t=s.save(value(schedule={'kind':'once','fireAt':stamp(clock[0]+timedelta(hours=1))}))
    clock[0]+=timedelta(hours=2);await s._tick();await asyncio.gather(*s._running.values())
    assert s.store.get(t['id'])['lastStatus']=='failed'
    assert s.store.get(t['id'])['nextRun'] is None
    with pytest.raises(ScheduleDomainError):s.run_now(t['id'])
    daily=s.save(value())
    for _ in range(52):
        s.run_now(daily['id']);await asyncio.gather(*s._running.values());clock[0]+=timedelta(seconds=1)
    assert len(s.store.runs(daily['id']))==50
    with s.store.db.connect() as c:
        assert c.execute("SELECT count(*) FROM threads WHERE scene='scheduled'").fetchone()[0]==51
    s.delete(daily['id'])
    with s.store.db.connect() as c:
        assert c.execute("SELECT count(*) FROM threads WHERE scene='scheduled'").fetchone()[0]==1


@pytest.mark.asyncio
async def test_preflight_failure_recorded_without_repeating(env):
    s,clock,engine=env;t=s.save(value());clock[0]+=timedelta(hours=2)
    def fail(*args,**kwargs):raise RuntimeError('sensitive provider detail')
    s.threads.create=fail
    await s._tick();await asyncio.gather(*s._running.values())
    assert s.store.runs(t['id'])[0]['status']=='failed'
    assert 'sensitive' not in s.store.runs(t['id'])[0]['error']
    await s._tick();assert not s._running


def test_enable_skips_disabled_time_limit_and_recovery(env):
    s,clock,_=env;t=s.save(value());s.set_enabled(t['id'],False)
    clock[0]+=timedelta(days=9);s.set_enabled(t['id'],True)
    assert s.store.get(t['id'])['nextRun']>stamp(clock[0])
    for i in range(19):s.save(value(name=str(i)))
    with pytest.raises(ScheduleDomainError) as e:s.save(value())
    assert e.value.code=='schedule_limit_reached'
    with pytest.raises(ScheduleDomainError):s.save(value(instruction='  '),t['id'])
    with pytest.raises(ScheduleDomainError):s.save(value(knowledgeLibraryIds=['ima:remote']),t['id'])
    run={'id':'r','threadId':None,'triggerKind':'schedule','startedAt':stamp(clock[0])}
    s.store.begin(run,t);s.recover()
    assert s.store.runs(t['id'])[0]['status']=='interrupted'


def test_dst_gap_and_fold():
    zone=ZoneInfo('America/New_York')
    spring=datetime(2026,3,8,6,tzinfo=timezone.utc)
    assert compute_next_run(Schedule(kind='daily',timeOfDay='02:30'),now=spring,zone=zone)=='2026-03-08T07:30:00.000000Z'
    fall=datetime(2026,11,1,5,31,tzinfo=timezone.utc)
    assert compute_next_run(Schedule(kind='daily',timeOfDay='01:30'),now=fall,zone=zone)=='2026-11-02T06:30:00.000000Z'


def test_migration_preserves_existing_threads_and_search_omits_scheduled(env):
    s,_,_=env
    regular=s.threads.create('同名','m','p')
    scheduled=s.threads.create('同名','m','p',scene='scheduled')
    assert [r.id for r,_ in ThreadRepository(s.store.db).search('同名')]==[regular.id]
    run_migrations(s.store.db)
    assert s.threads.get(scheduled.id).scene=='scheduled'


@pytest.mark.asyncio
async def test_running_events_are_forwarded_to_shared_bus(env):
    s,_,engine=env;events=[]
    async def emit(event):events.append(event)
    s.emit=emit
    async def live_run(turn_id,callback):
        await callback('persisted-event')
        return SimpleNamespace(status=SimpleNamespace(value='completed'),error_code=None)
    engine.run=live_run
    t=s.save(value());s.run_now(t['id']);await asyncio.gather(*s._running.values())
    assert events==['persisted-event']
