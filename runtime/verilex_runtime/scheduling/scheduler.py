"""Application-lifetime scheduler.

Claim-before-spawn, overlap and catch-up structure adapted from OpenWorker
coworker/automation/scheduler.py, 5bc10d928e0b64aae74313349a3b17bd19643ae2.
Copyright (c) 2024 Andrew Ng. MIT; see THIRD_PARTY_NOTICES.md.
SQLite state, bounded runs and VeriLex engine integration are local changes.
"""
import asyncio
import logging
from uuid import uuid4
from .models import Schedule, TaskInput, compute_next_run, utc_now, stamp, parse, local_zone
from .errors import ScheduleDomainError, invalid

logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self, store, threads, engine, *, audit=None, now=utc_now, zone=None, tick_seconds=30, knowledge=None, workspaces=None):
        self.store, self.threads, self.engine = store, threads, engine
        self.audit, self.now, self.zone = audit, now, zone or local_zone()
        self.knowledge, self.workspaces = knowledge, workspaces
        self.tick_seconds = tick_seconds
        self.emit = None
        self._task = None
        self._running = {}
        self._started_at = stamp(now())
        self._closing = False

    def _audit(self, identifier, outcome):
        if self.audit:
            self.audit.record_schedule(schedule_id=identifier, outcome=outcome)

    def _next(self, task):
        return compute_next_run(Schedule(**task['schedule']), now=self.now(), zone=self.zone, enabled=task['enabled'], run_count=task.get('scheduleRunCount', task['runCount']))

    def save(self, value: TaskInput, identifier=None):
        self._idle(identifier)
        data = value.model_dump()
        data['instruction'] = data['instruction'].strip()
        data['name'] = data['name'].strip() or data['instruction'][:20]
        if not data['instruction'] or not data['model'].strip():
            raise invalid()
        if any(not i or i.startswith('ima:') for i in data['knowledgeLibraryIds']):
            raise invalid('定时任务只能绑定本地知识库。')
        if data['knowledgeLibraryIds']:
            if not self.knowledge:
                raise invalid('知识库不可用。')
            self.knowledge.validate_selection(data['knowledgeLibraryIds'])
        if data['workspaceId'] and self.workspaces:
            self.workspaces.get(data['workspaceId'])
        old = self.store.get(identifier) if identifier else None
        if old and (old['providerId'], old['model']) != (data['providerId'], data['model']):
            raise invalid('任务沿用创建时的模型。请新建任务以更换模型。')
        if value.schedule.kind == 'once' and parse(value.schedule.fireAt) <= self.now():
            raise invalid('请选择将来的运行时间。')
        task = (old or {'id': str(uuid4()), 'enabled': True, 'createdAt': stamp(self.now()), 'runCount': 0, 'lastStatus': None, 'lastRunAt': None}) | data
        # Total history belongs to the task; completion belongs to its current
        # schedule. Old JSON rows retain their previous completion semantics.
        task['scheduleRunCount'] = (old.get('scheduleRunCount', old['runCount'])
                                    if old and old['schedule'] == data['schedule'] else 0)
        task['updatedAt'] = stamp(self.now())
        task['nextRun'] = self._next(task)
        self.store.save(task, create=old is None)
        self._audit(task['id'], 'updated' if old else 'created')
        return task

    def _idle(self, identifier=None):
        if self._closing or (identifier is not None and identifier in self._running):
            raise ScheduleDomainError('schedule_run_conflict', '任务正在运行，请结束后再操作。', 409)

    def set_enabled(self, identifier, enabled):
        task = self.store.get(identifier)
        task['enabled'] = enabled
        task['nextRun'] = self._next(task)
        task['updatedAt'] = stamp(self.now())
        self.store.save(task)
        self._audit(identifier, 'enabled' if enabled else 'disabled')
        return task

    def delete(self, identifier):
        self._idle(identifier)
        self.store.delete(identifier)
        self._audit(identifier, 'deleted')

    def run_now(self, identifier, trigger='manual'):
        task = self.store.get(identifier)
        self._idle(identifier)
        if self._running or not task['enabled'] or (task['schedule']['kind'] == 'once' and task.get('scheduleRunCount', task['runCount'])):
            raise ScheduleDomainError('schedule_run_conflict', '已有任务在运行，或此任务已停用、已完成。', 409)
        run = {'id': str(uuid4()), 'taskId': identifier, 'threadId': None, 'turnId': None, 'triggerKind': trigger, 'status': 'running', 'startedAt': stamp(self.now()), 'finishedAt': None, 'error': None}
        task['scheduleRunCount'] = task.get('scheduleRunCount', task['runCount']) + 1
        task['runCount'] += 1
        task['lastRunAt'], task['lastStatus'] = run['startedAt'], 'running'
        task['nextRun'] = self._next(task)
        self.store.begin(run, task)
        self._running[identifier] = asyncio.create_task(self._execute(task, run))
        self._audit(identifier, 'triggered')
        return run

    async def _execute(self, task, run):
        status, error = 'failed', '任务未能完成，请检查模型配置后重试。'
        try:
            thread = self.threads.create(task['name'], task['model'], task['providerId'], task['workspaceId'], scene='scheduled')
            run['threadId'] = thread.id
            self.store.attach_thread(run['id'], thread.id)
            turn = self.engine.start(run['threadId'], task['instruction'], approval_mode='ask', knowledge_library_ids=task['knowledgeLibraryIds'], wall_clock_seconds=task['maxMinutes'] * 60)
            self.store.attach_turn(run['id'], turn.id)
            async def emit(event):
                if self.emit is not None:
                    await self.emit(event)  # Engine persists before publishing.
            result = await self.engine.run(turn.id, emit)
            status = {'completed': 'succeeded', 'failed': 'failed', 'cancelled': 'interrupted', 'interrupted': 'interrupted'}.get(result.status.value, 'failed')
            error = None if status == 'succeeded' else ('运行超过时长上限。' if result.error_code == 'turn_limit_exceeded' else '运行未完成，请打开记录查看详情。')
        except asyncio.CancelledError:
            status, error = 'interrupted', '应用退出，运行已中断。'
            raise
        except Exception:
            logger.warning('Scheduled run failed to start or finish')
        finally:
            try:
                self.store.finish(run['id'], status, error, stamp(self.now()))
                latest = self.store.get(task['id'])
                latest['lastStatus'] = status
                latest['nextRun'] = self._next(latest)
                self.store.save(latest)
            finally:
                self._running.pop(task['id'], None)

    def recover(self):
        self.store.recover(stamp(self.now()))
        self._audit('scheduler', 'recovered')

    def start(self):
        if self._task is None:
            self._closing = False
            self._started_at = stamp(self.now())
            self._task = asyncio.create_task(self._loop())

    async def _tick(self):
        for task in self.store.due(stamp(self.now())):
            if self._running or self._closing:
                break
            self.run_now(task['id'], 'catchup' if task['nextRun'] < self._started_at else 'schedule')

    async def _loop(self):
        while True:
            try:
                await self._tick()
            except Exception:
                logger.warning('Scheduled task tick failed')
            await asyncio.sleep(self.tick_seconds)

    async def close(self):
        self._closing = True
        tasks = ([self._task] if self._task else []) + list(self._running.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._task = None
