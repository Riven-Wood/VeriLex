import type { ContractReviewExport } from '../runtime/contractReviewClient';
import { saveDocumentExport } from './documentExport';

export const saveContractReview = (result: ContractReviewExport): Promise<string | null> =>
  saveDocumentExport(result, 'save_contract_review');

/** Match the native copy-name boundary, including its UTF-8 byte limit. */
export function wordCopyFilename(filename: string): string {
  const suffix = '（修改副本）.docx';
  const encoder = new TextEncoder();
  const budget = 240 - encoder.encode(suffix).length;
  const name = filename.replace(/\.docx?$/i, '')
    .replace(/[\u0000-\u001f\u007f-\u009f/\\:*?"<>|\u202a-\u202e\u2066-\u2069]/g, '_')
    .trim().replace(/^\.+/, '');
  let stem = '', size = 0;
  for (const char of name) {
    size += encoder.encode(char).length;
    if (size > budget) break;
    stem += char;
  }
  return `${stem.trim() || '合同'}${suffix}`;
}
