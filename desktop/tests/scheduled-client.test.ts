import { afterEach, expect, test, vi } from 'vitest';
import { scheduledApi } from '../src/runtime/scheduledClient';
afterEach(()=>vi.unstubAllGlobals());
test('scheduled client uses authenticated runtime transport and encoded identifiers',async()=>{
 const fetch=vi.fn(async()=>new Response(JSON.stringify([]),{status:200}));vi.stubGlobal('fetch',fetch);
 await scheduledApi.runs('task/id');expect(fetch.mock.calls[0][0]).toContain('/v1/schedules/task%2Fid/runs');
});
