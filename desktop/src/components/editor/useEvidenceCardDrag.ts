import {useEffect,useRef,useState,type PointerEvent as ReactPointerEvent,type RefObject} from 'react';

export interface EvidenceDrop {path:string|null;group:string;place:'before'|'after'}
// Match the home conversation list: pointer input for WKWebView, insertion edges,
// dimmed source, and frame-driven edge scrolling. Commit the order only on drop.
export function useEvidenceCardDrag(board:RefObject<HTMLDivElement>,enabled:boolean,onDrop:(path:string,target:EvidenceDrop)=>void){
 const [dragPath,setDragPath]=useState<string|null>(null),[edge,setEdge]=useState<EvidenceDrop|null>(null);
 const dropRef=useRef(onDrop);dropRef.current=onDrop;
 const drag=useRef<{path:string;pointerId:number;edge:EvidenceDrop|null;active:boolean;cleanup:()=>void}|null>(null);
 const finish=(cancelled:boolean)=>{const state=drag.current;if(!state)return;drag.current=null;state.cleanup();if(!cancelled&&state.active&&state.edge)dropRef.current(state.path,state.edge);};
 useEffect(()=>()=>finish(true),[enabled]);
 const locate=(x:number,y:number)=>{
  const state=drag.current,root=board.current;if(!state||!state.active||!root)return;
  const hit=document.elementFromPoint?.(x,y);let next:EvidenceDrop|null=null;
  if(hit&&root.contains(hit)){
   const section=hit.closest<HTMLElement>('[data-group-drop]');
   if(section){
    let row=hit.closest<HTMLElement>('[data-evidence-path]');
    if(!row){const rows=[...section.querySelectorAll<HTMLElement>('[data-evidence-path]')];row=rows.find(item=>item.getBoundingClientRect().bottom>y)??rows.at(-1)??null;}
    if(row?.dataset.evidencePath!==state.path){
     const bounds=row?.getBoundingClientRect();
     next={path:row?.dataset.evidencePath??null,group:section.dataset.groupDrop??'',place:bounds&&y<bounds.top+bounds.height/2?'before':'after'};
    }
   }
  }
  if(JSON.stringify(next)!==JSON.stringify(state.edge)){state.edge=next;setEdge(next);}
 };
 const start=(event:ReactPointerEvent,path:string)=>{
  if(!enabled||(event.button!==undefined&&event.button!==0))return;
  event.preventDefault();finish(true);
  let x=event.clientX,y=event.clientY,frame=0,last=0;
  const origin={x,y};
  const move=(e:PointerEvent)=>{const state=drag.current;if(!state||e.pointerId!==state.pointerId)return;x=e.clientX;y=e.clientY;
   if(!state.active&&Math.hypot(x-origin.x,y-origin.y)>=4){state.active=true;setDragPath(path);}
   locate(x,y);
  };
  const up=(e:PointerEvent)=>{if(e.pointerId!==drag.current?.pointerId)return;if(e.type==='pointerup')locate(e.clientX,e.clientY);finish(e.type==='pointercancel');};
  const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.preventDefault();finish(true);}};
  const blur=()=>finish(true);
  const cleanup=()=>{cancelAnimationFrame(frame);window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',up);window.removeEventListener('keydown',key);window.removeEventListener('blur',blur);setDragPath(null);setEdge(null);};
  const tick=(time:number)=>{
   const state=drag.current,scroll=board.current?.closest<HTMLElement>('.litigation-page-content');if(!state)return;
   const elapsed=last?Math.min(time-last,32):16;last=time;
   if(state.active&&scroll){const r=scroll.getBoundingClientRect(),size=Math.min(48,r.height/3);
    if(size>0&&x>=r.left&&x<=r.right){const speed=y<r.top+size?-Math.min(1,(r.top+size-y)/size):y>r.bottom-size?Math.min(1,(y-r.bottom+size)/size):0;
     if(speed){scroll.scrollTop+=speed*elapsed*0.6;locate(x,Math.max(r.top+1,Math.min(r.bottom-1,y)));}
    }
   }
   frame=requestAnimationFrame(tick);
  };
  drag.current={path,pointerId:event.pointerId,edge:null,active:false,cleanup};
  window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);window.addEventListener('pointercancel',up);window.addEventListener('keydown',key);window.addEventListener('blur',blur);
  frame=requestAnimationFrame(tick);
 };
 return {dragPath,edge,start};
}
