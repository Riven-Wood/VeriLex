export interface ToolCitationSource { sourceId: string; title: string; number: number; plugin?: boolean; hidden?: boolean; unavailable?: boolean; url?: string }
export type ToolCitationPart = {kind: 'text'; text: string} | {kind: 'source'; source: ToolCitationSource};

export function renderToolCitationText(text: string, sources: readonly ToolCitationSource[], streaming = false): ToolCitationPart[] {
  const known = new Map(sources.map(source => [source.sourceId, source]));
  const parts: ToolCitationPart[] = [];
  let cursor = 0;
  for (const match of text.matchAll(/\[\[source:([^\]\n]{1,400})\]\]/g)) {
    if (match.index! > cursor) parts.push({kind:'text', text:text.slice(cursor,match.index)});
    const source = known.get(match[1]);
    if (!streaming && source?.unavailable) parts.push({kind:'text', text:'〔原文链接未取得〕'});
    else if (!streaming && !source?.hidden) parts.push(source ? {kind:'source', source} : {kind:'text',text:'〔来源未验证〕'});
    cursor = match.index! + match[0].length;
  }
  let tail = text.slice(cursor);
  if (streaming) {
    const start = tail.lastIndexOf('[['), prefix = '[[source:';
    if (start >= 0 && (prefix.startsWith(tail.slice(start)) || tail.slice(start).startsWith(prefix))) tail = tail.slice(0,start);
  } else if (tail.includes('[[source:')) tail = tail.slice(0,tail.indexOf('[[source:')) + '〔来源未验证〕';
  if (tail) parts.push({kind:'text',text:tail});
  return parts;
}
