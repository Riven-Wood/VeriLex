import { encodeWord, type WordDocument } from '../../runtime/wordDocumentClient';

const DATABASE = 'verilex-standalone-drafting';
const KEY = 'current';
let leaveGuard: (() => Promise<void>) | null = null;
export function setStandaloneDraftGuard(guard: (() => Promise<void>) | null) { leaveGuard = guard; }
export function getStandaloneDraftGuard() { return leaveGuard; }

function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => request.result.createObjectStore('drafts');
    request.onerror = () => reject(new Error('无法打开本机文书草稿，请检查本机存储。'));
    request.onsuccess = () => resolve(request.result);
  });
}
export async function loadStandaloneDraft(): Promise<WordDocument> {
  const db = await database();
  let value: WordDocument | undefined;
  try {
    value = await new Promise((resolve, reject) => {
      const request = db.transaction('drafts', 'readonly').objectStore('drafts').get(KEY);
      request.onsuccess = () => resolve(request.result as WordDocument | undefined);
      request.onerror = () => reject(new Error('无法读取本机文书草稿。'));
    });
  } finally { db.close(); }
  if (value) return value;
  const response = await fetch('/blank-drafting.docx');
  if (!response.ok) throw new Error('无法打开空白 Word 文书，请重试。');
  return { id: 'standalone-drafting', filename: '未命名文书.docx', content: encodeWord(new Uint8Array(await response.arrayBuffer())), revision: 0, updated_at: '' };
}
export async function saveStandaloneDraft(document: WordDocument, revision: number, bytes: Uint8Array): Promise<{id:string;revision:number}> {
  const next = {...document, revision: revision + 1, content: encodeWord(bytes), updated_at: new Date().toISOString()};
  const db = await database();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction('drafts', 'readwrite');
      tx.objectStore('drafts').put(next, KEY);
      tx.oncomplete = () => resolve();
      tx.onabort = tx.onerror = () => reject(new Error('文书草稿未能保存到本机，请重试或另存 Word。'));
    });
  } finally { db.close(); }
  return { id: document.id, revision: next.revision };
}
