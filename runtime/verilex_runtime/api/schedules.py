from fastapi import APIRouter
from verilex_runtime.scheduling.models import TaskInput
from verilex_runtime.scheduling.errors import ScheduleDomainError


def build_schedules_router(scheduler):
    router = APIRouter(prefix='/v1/schedules', tags=['schedules'])

    @router.get('')
    async def list_tasks():
        return scheduler.store.list()

    @router.post('', status_code=201)
    async def create_task(body: TaskInput):
        return scheduler.save(body)

    @router.patch('/{identifier}')
    async def update_task(identifier: str, body: TaskInput):
        return scheduler.save(body, identifier)

    @router.post('/{identifier}/enable')
    async def enable(identifier: str):
        return scheduler.set_enabled(identifier, True)

    @router.post('/{identifier}/disable')
    async def disable(identifier: str):
        return scheduler.set_enabled(identifier, False)

    @router.post('/{identifier}/run', status_code=202)
    async def run(identifier: str):
        return scheduler.run_now(identifier)

    @router.get('/{identifier}/runs')
    async def runs(identifier: str):
        return scheduler.store.runs(identifier)

    @router.get('/{identifier}/runs/{run_id}')
    async def get_run(identifier: str, run_id: str):
        for item in scheduler.store.runs(identifier):
            if item['id'] == run_id:
                return item
        raise ScheduleDomainError('schedule_run_not_found', '运行记录不存在。', 404)

    @router.delete('/{identifier}', status_code=204)
    async def delete(identifier: str):
        scheduler.delete(identifier)

    return router
