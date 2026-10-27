import { runtimeRequest } from './api';
export interface ImaChoice { id: string; name: string }
export interface ImaChoices { items: ImaChoice[]; next_cursor: string | null }
const post = (body: unknown) => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
export const imaApi = {
  status: () => runtimeRequest<{configured: boolean}>('/v1/ima'),
  connect: (client_id: string, api_key: string) => runtimeRequest<ImaChoices & {token: string}>('/v1/ima/connect', post({client_id, api_key})),
  choices: (token: string, q: string, cursor = '') => runtimeRequest<ImaChoices>(`/v1/ima/choices?${new URLSearchParams({token, q, cursor})}`),
  save: (token: string, ids: string[]) => runtimeRequest('/v1/ima/selection', post({token, ids})),
  resume: () => runtimeRequest<ImaChoices & {token: string; selected: string[]}>('/v1/ima/resume', {method:'POST'}),
  disconnect: () => runtimeRequest('/v1/ima', {method: 'DELETE'}),
};
