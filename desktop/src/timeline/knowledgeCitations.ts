export interface KnowledgeCitationSource {sourceId:string; cardId:string; title:string; number:number}
export type KnowledgeCitationPart = {kind:'text'; text:string}|{kind:'knowledge'; source:KnowledgeCitationSource};

/** Only a successfully saved quote from this turn may provide a destination. */
export function renderKnowledgeCitationText(text:string,sources:readonly KnowledgeCitationSource[],streaming=false):KnowledgeCitationPart[] {
  const known=new Map(sources.map(source=>[source.sourceId,source]));
  const parts:KnowledgeCitationPart[]=[];let cursor=0;
  const pattern=/\[\[knowledge:([^\]\n]{1,400})\]\]/g;
  for(const match of text.matchAll(pattern)) {
    if(match.index!>cursor)parts.push({kind:'text',text:text.slice(cursor,match.index)});
    const source=known.get(match[1]);
    if(!streaming)parts.push(source?{kind:'knowledge',source}:{kind:'text',text:'〔知识库引用未验证〕'});
    cursor=match.index!+match[0].length;
  }
  let tail=text.slice(cursor);
  if(streaming) {
    const prefix='[[knowledge:';
    const start=tail.lastIndexOf('[[');
    if(start>=0&&(prefix.startsWith(tail.slice(start))||tail.slice(start).startsWith(prefix)))tail=tail.slice(0,start);
  } else if(tail.includes('[[knowledge:'))tail=tail.slice(0,tail.indexOf('[[knowledge:'))+'〔知识库引用未验证〕';
  if(tail)parts.push({kind:'text',text:tail});
  return parts;
}
