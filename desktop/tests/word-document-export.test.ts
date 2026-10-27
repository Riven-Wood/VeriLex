import {expect, test, vi} from 'vitest';
import {invoke} from '@tauri-apps/api/core';
import {saveContractReview, wordCopyFilename} from '../src/platform/contractReviewExport';

vi.mock('@tauri-apps/api/core', () => ({invoke: vi.fn().mockImplementation(async (_command, args) => {
  if (!args.defaultName.endsWith('（修改副本）.docx')) throw '文件名无效';
  return '/synthetic/copy.docx';
})}));

test('whole Word exports use the native accepted copy suffix and exact edited bytes', async () => {
  vi.stubGlobal('__TAURI_INTERNALS__', {});
  try {
    await expect(saveContractReview({filename: wordCopyFilename('合同.DOC'), content:'QUJD'})).resolves.toBe('/synthetic/copy.docx');
    expect(invoke).toHaveBeenCalledWith('save_contract_review', {defaultName:'合同（修改副本）.docx',content:[65,66,67]});
  } finally { vi.unstubAllGlobals(); }
});

test('copy suggestions remain safe for native saves with long Chinese or unusual names', () => {
  for (const filename of ['合同'.repeat(100)+'.docx', '..合同\n:测试\u202e.docx', '.docx']) {
    const name=wordCopyFilename(filename);
    expect(new TextEncoder().encode(name).length).toBeLessThanOrEqual(240);
    expect(name).toMatch(/.+（修改副本）\.docx$/);
    expect(name).not.toMatch(/^[.\s]|[\u0000-\u001f/\\:*?"<>|\u202e]/);
  }
});
