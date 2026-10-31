import React, {useMemo, useState} from 'react';
import type {LitigationSource} from './LitigationClient';
import './ProjectRelationshipGraph.css';

export interface ProjectRelationEntry {
  title:string; other:string; detail:string; date?:string; pending:string[]; sources:LitigationSource[];
}
interface Node {name:string; lines:string[]; x:number; y:number; height:number}
const NODE_WIDTH=200;
function wrapName(name:string):string[] {
  const chars=Array.from(name);const lines:string[]=[];
  while(chars.length)lines.push(chars.splice(0,13).join(''));
  return lines.length?lines:['待核实'];
}
function edgePoint(a:Node,b:Node):[number,number] {
  const dx=b.x-a.x,dy=b.y-a.y;
  const factor=Math.min(dx===0?Infinity:(NODE_WIDTH/2)/Math.abs(dx),dy===0?Infinity:(a.height/2)/Math.abs(dy));
  return [a.x+dx*factor,a.y+dy*factor];
}

/** Draws shared entities once; relationship text and sources remain fully readable below. */
export function ProjectRelationshipGraph({entries,onOpenSource}:{entries:ProjectRelationEntry[];onOpenSource?:(source:LitigationSource)=>void}) {
  const [focus,setFocus]=useState<string|null>(null);
  const [zoom,setZoom]=useState(1);
  const layout=useMemo(()=>{
    const names:string[]=[];
    const index=(name:string)=>{const found=names.indexOf(name);if(found!==-1)return found;names.push(name);return names.length-1;};
    const edges=entries.map((entry,i)=>({entry,i,from:index(entry.title.trim()),to:entry.other.trim()?index(entry.other.trim()):-1}));
    const maxHeight=Math.max(64,...names.map(name=>wrapName(name).length*20+30));
    const radiusX=Math.max(245,names.length*57),radiusY=Math.max(155,names.length*(maxHeight+20)/5.4);
    const width=Math.ceil(radiusX*2+NODE_WIDTH+60),height=Math.ceil(radiusY*2+maxHeight+60);
    const nodes:Node[]=names.map((name,i)=>{const angle=2*Math.PI*i/Math.max(names.length,1)-Math.PI/2;const lines=wrapName(name);return{name,lines,x:width/2+radiusX*Math.cos(angle),y:height/2+radiusY*Math.sin(angle),height:Math.max(64,lines.length*20+30)}});
    return{nodes,edges,width,height};
  },[entries]);
  const graphable=layout.nodes.length<=40 && layout.edges.some(edge=>edge.to>=0&&edge.from!==edge.to);
  return <section className="project-relations" aria-label="法律关系图">
    {graphable ? <>
      <div className="project-relations-actions"><span>点击主体可突出相关关系，编号对应下方说明。</span><button type="button" aria-label="缩小关系图" disabled={zoom<=0.6} onClick={()=>setZoom(value=>Math.max(.6,Number((value-.2).toFixed(1))))}>−</button><span>{Math.round(zoom*100)}%</span><button type="button" aria-label="放大关系图" disabled={zoom>=1.6} onClick={()=>setZoom(value=>Math.min(1.6,Number((value+.2).toFixed(1))))}>＋</button></div>
      <div className="project-relations-canvas" tabIndex={0} aria-label="关系图，可滚动查看">
        <svg role="img" aria-label="主体与关系网络" viewBox={`0 0 ${layout.width} ${layout.height}`} style={{width:Math.round(layout.width*zoom),height:Math.round(layout.height*zoom)}}>
          {layout.edges.map(edge=>{
            if(edge.to<0||edge.from===edge.to)return null;
            const a=layout.nodes[edge.from],b=layout.nodes[edge.to];const [x1,y1]=edgePoint(a,b),[x2,y2]=edgePoint(b,a);
            const dx=x2-x1,dy=y2-y1,len=Math.max(1,Math.hypot(dx,dy));
            const pair=layout.edges.filter(item=>item.to>=0&&((item.from===edge.from&&item.to===edge.to)||(item.from===edge.to&&item.to===edge.from)));
            const pos=pair.findIndex(item=>item.i===edge.i),offset=(pos-(pair.length-1)/2)*70*(edge.from<edge.to?1:-1);
            const mx=(x1+x2)/2-dy/len*offset,my=(y1+y2)/2+dx/len*offset;
            const lx=(x1+2*mx+x2)/4,ly=(y1+2*my+y2)/4;
            const selected=!focus||focus===a.name||focus===b.name;
            return <g key={edge.i} className="project-relations-edge" data-dimmed={!selected || undefined}><path d={`M ${x1} ${y1} Q ${mx} ${my} ${x2} ${y2}`}/><circle cx={lx} cy={ly} r="14"/><text x={lx} y={ly+4} textAnchor="middle">{edge.i+1}</text></g>;
          })}
          {layout.nodes.map(node=><g key={node.name} role="button" tabIndex={0} aria-label={`主体 ${node.name}`} aria-pressed={focus===node.name} className="project-relations-node" data-active={focus===node.name || undefined} onClick={()=>setFocus(old=>old===node.name?null:node.name)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();setFocus(old=>old===node.name?null:node.name);}}}>
            <rect x={node.x-NODE_WIDTH/2} y={node.y-node.height/2} width={NODE_WIDTH} height={node.height} rx="10"/>
            <text textAnchor="middle" x={node.x} y={node.y-(node.lines.length-1)*10+5}>{node.lines.map((line,i)=><tspan key={i} x={node.x} dy={i?20:0}>{line}</tspan>)}</text>
          </g>)}
        </svg>
      </div>
    </> : <p className="project-relations-note">{layout.nodes.length>40?'关系较多，请查看下方完整清单。':'材料中尚无可连接的双方主体，已保留以下记录供核对。'}</p>}
    <ol className="project-relations-list">{layout.edges.map(edge=><li key={edge.i} data-dimmed={!!focus && edge.entry.title.trim()!==focus && edge.entry.other.trim()!==focus || undefined}>
      <div><span className="project-relations-number">{edge.i+1}</span><strong>{edge.entry.title}</strong><span> — </span><strong>{edge.entry.other || '待核实'}</strong></div>
      {edge.entry.date&&<time>{edge.entry.date}</time>}
      {edge.entry.detail&&<p>{edge.entry.detail}</p>}
      {edge.entry.pending.map((item,i)=><p key={i}>待核实：{item}</p>)}
      {edge.entry.sources.map((source,i)=><button type="button" key={i} onClick={()=>onOpenSource?.(source)}>出处：{source.path}{source.page?` · 第 ${source.page} 页`:''}</button>)}
    </li>)}</ol>
  </section>;
}
