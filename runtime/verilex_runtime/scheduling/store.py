import json
from .errors import ScheduleDomainError


class ScheduleStore:
    def __init__(self, database):
        self.db = database

    def list(self):
        with self.db.connect() as c:
            return [json.loads(r['data']) | {'enabled': bool(r['enabled']), 'nextRun': r['next_run']} for r in c.execute('SELECT * FROM scheduled_tasks ORDER BY created_at, id')]

    def get(self, identifier):
        for task in self.list():
            if task['id'] == identifier:
                return task
        raise ScheduleDomainError('schedule_not_found', '定时任务不存在。', 404)

    def save(self, task, *, create=False):
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if create and c.execute('SELECT COUNT(*) FROM scheduled_tasks').fetchone()[0] >= 20:
                raise ScheduleDomainError('schedule_limit_reached', '最多可保存20个定时任务。')
            if create:
                c.execute('INSERT INTO scheduled_tasks(id,data,enabled,next_run,created_at) VALUES(?,?,?,?,?)', (task['id'], json.dumps(task), task['enabled'], task['nextRun'], task['createdAt']))
            else:
                c.execute('UPDATE scheduled_tasks SET data=?,enabled=?,next_run=? WHERE id=?', (json.dumps(task), task['enabled'], task['nextRun'], task['id']))

    def due(self, now):
        with self.db.connect() as c:
            return [json.loads(r['data']) | {'enabled': bool(r['enabled']), 'nextRun': r['next_run']} for r in c.execute('SELECT * FROM scheduled_tasks WHERE enabled=1 AND next_run<=? ORDER BY next_run,id', (now,))]

    def runs(self, identifier):
        self.get(identifier)
        with self.db.connect() as c:
            return [dict(r) for r in c.execute('SELECT id, task_id AS taskId, thread_id AS threadId, turn_id AS turnId, trigger_kind AS triggerKind, status, started_at AS startedAt, finished_at AS finishedAt, error FROM scheduled_task_runs WHERE task_id=? ORDER BY started_at DESC, rowid DESC LIMIT 50', (identifier,))]

    def begin(self, run, task):
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('INSERT INTO scheduled_task_runs(id,task_id,thread_id,trigger_kind,status,started_at) VALUES(?,?,?,?,?,?)', (run['id'],task['id'],run['threadId'],run['triggerKind'],'running',run['startedAt']))
            c.execute('UPDATE scheduled_tasks SET data=?,next_run=? WHERE id=?', (json.dumps(task),task['nextRun'],task['id']))
            old = c.execute('SELECT id,thread_id FROM scheduled_task_runs WHERE task_id=? ORDER BY started_at DESC,rowid DESC LIMIT -1 OFFSET 50', (task['id'],)).fetchall()
            for row in old:
                c.execute('DELETE FROM scheduled_task_runs WHERE id=?', (row['id'],))
                if row['thread_id']:
                    c.execute("DELETE FROM threads WHERE id=? AND scene='scheduled'", (row['thread_id'],))

    def attach_thread(self, run_id, thread_id):
        with self.db.connect() as c:
            c.execute('UPDATE scheduled_task_runs SET thread_id=? WHERE id=?', (thread_id,run_id))

    def attach_turn(self, run_id, turn_id):
        with self.db.connect() as c:
            c.execute('UPDATE scheduled_task_runs SET turn_id=? WHERE id=?', (turn_id,run_id))

    def finish(self, run_id, status, error, now):
        with self.db.connect() as c:
            c.execute('UPDATE scheduled_task_runs SET status=?,error=?,finished_at=? WHERE id=?', (status,error,now,run_id))

    def recover(self, now):
        with self.db.connect() as c:
            rows = c.execute("SELECT DISTINCT task_id FROM scheduled_task_runs WHERE status='running'").fetchall()
            c.execute("UPDATE scheduled_task_runs SET status='interrupted',error='应用退出，运行已中断。',finished_at=? WHERE status='running'", (now,))
        for row in rows:
            task = self.get(row['task_id'])
            task['lastStatus'] = 'interrupted'
            self.save(task)

    def delete(self, identifier):
        runs = self.runs(identifier)
        with self.db.connect() as c:
            c.execute('DELETE FROM scheduled_tasks WHERE id=?', (identifier,))
            for run in runs:
                if run['threadId']:
                    c.execute("DELETE FROM threads WHERE id=? AND scene='scheduled'", (run['threadId'],))
