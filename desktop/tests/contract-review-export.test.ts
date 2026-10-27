import {afterEach, expect, test, vi} from 'vitest';
import {invoke} from '@tauri-apps/api/core';
import {saveContractReview} from '../src/platform/contractReviewExport';
import {saveRedaction} from '../src/platform/redactionExport';

vi.mock('@tauri-apps/api/core', () => ({invoke: vi.fn()}));
afterEach(() => {vi.clearAllMocks(); vi.unstubAllGlobals();});

test('合同批注版选择专用原生命令，并保留原件字节', async () => {
  vi.stubGlobal('__TAURI_INTERNALS__', {});
  vi.mocked(invoke).mockResolvedValue('/tmp/合同（批注版）.docx');
  expect(await saveContractReview({filename: '合同（批注版）.docx', content: 'UEsDBA=='})).toBe('/tmp/合同（批注版）.docx');
  expect(invoke).toHaveBeenCalledWith('save_contract_review', {defaultName: '合同（批注版）.docx', content: [80, 75, 3, 4]});
});

test('取消保存返回null，脱敏继续使用原有命令', async () => {
  vi.stubGlobal('__TAURI_INTERNALS__', {});
  vi.mocked(invoke).mockResolvedValue(null);
  expect(await saveContractReview({filename: '合同（批注版）.docx', content: ''})).toBeNull();
  expect(await saveRedaction({filename: '脱敏版.docx', content: ''})).toBeNull();
  expect(invoke).toHaveBeenLastCalledWith('save_redaction', {defaultName: '脱敏版.docx', content: []});
});


test('脱敏将选中的目录交给专用保存命令', async () => {
 vi.stubGlobal('__TAURI_INTERNALS__', {});
 vi.mocked(invoke).mockResolvedValue('/tmp/交付/脱敏版（1）.docx');
 expect(await saveRedaction({filename:'脱敏版.docx',content:'UEsDBA=='},'/tmp/交付')).toBe('/tmp/交付/脱敏版（1）.docx');
 expect(invoke).toHaveBeenCalledWith('save_redaction',{defaultName:'脱敏版.docx',content:[80,75,3,4],directory:'/tmp/交付'});
});

test('审查意见书独立命名并复用不覆盖原文件的保存命令', async () => {
  vi.stubGlobal('__TAURI_INTERNALS__', {});
  vi.mocked(invoke).mockResolvedValue('/tmp/合同（审查意见书）.docx');
  expect(await saveContractReview({filename: '合同（审查意见书）.docx', content: 'UEsDBA=='})).toBe('/tmp/合同（审查意见书）.docx');
  expect(invoke).toHaveBeenCalledWith('save_contract_review', {defaultName: '合同（审查意见书）.docx', content: [80, 75, 3, 4]});
});
