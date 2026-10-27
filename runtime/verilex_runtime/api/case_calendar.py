from fastapi import APIRouter, Query

from verilex_runtime.case_calendar.models import EventInput, EventUpdate, ReminderAcknowledgement
from verilex_runtime.case_calendar.store import CaseCalendarStore


def build_case_calendar_router(store: CaseCalendarStore) -> APIRouter:
    router = APIRouter(prefix="/v1/case-calendar", tags=["case-calendar"])

    @router.get("/events")
    async def list_events():
        return store.list()

    @router.post("/events", status_code=201)
    async def create_event(body: EventInput):
        return store.create(body)

    @router.get("/events/{identifier}")
    async def get_event(identifier: str):
        return store.get(identifier)

    @router.patch("/events/{identifier}")
    async def update_event(identifier: str, body: EventUpdate):
        return store.update(identifier, body)

    @router.delete("/events/{identifier}", status_code=204)
    async def delete_event(identifier: str, revision: int = Query(ge=1)):
        store.delete(identifier, revision)

    @router.get("/reminders")
    async def list_reminders():
        return store.reminders()

    @router.post("/reminders/claim")
    async def claim_reminders():
        return {"count": store.claim()}

    @router.post("/reminders/{identifier}/ack")
    async def acknowledge_reminder(identifier: str, body: ReminderAcknowledgement):
        return store.acknowledge(identifier, body.revision)

    return router
