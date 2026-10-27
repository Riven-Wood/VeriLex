import {Extension} from '@tiptap/core';
import {Plugin,PluginKey} from '@tiptap/pm/state';
import {Decoration,DecorationSet} from '@tiptap/pm/view';
export const proposalMarkKey=new PluginKey<DecorationSet>('litigationProposal');
export const ProposalMarks=Extension.create({name:'litigationProposal',addProseMirrorPlugins(){return [new Plugin({
 key:proposalMarkKey,
 state:{init:()=>DecorationSet.empty,apply(tr,previous){
  const mark=tr.getMeta(proposalMarkKey) as {from:number;to:number;text:string;commentId?:string}[]|null|undefined;
  if(mark===null||tr.docChanged)return DecorationSet.empty;
  if(mark===undefined)return previous;
  const decorations=[];
  for(const item of mark){
   if(item.from<0||item.to>tr.doc.content.size||item.from>item.to)continue;
   if(item.from<item.to)decorations.push(Decoration.inline(item.from,item.to,{class:item.commentId?'litigation-comment-anchor':'litigation-proposed-deletion'}));
   decorations.push(Decoration.widget(item.to,()=>{if(item.commentId){const node=document.createElement('button');node.type='button';node.className='litigation-comment-number';node.textContent='批注';node.onclick=()=>document.getElementById(item.commentId!)?.scrollIntoView({block:'nearest'});return node;}const node=document.createElement('ins');node.className='litigation-proposed-insertion';node.textContent=item.text;node.setAttribute('aria-label','建议新增文字');return node;},{side:1}));
  }
  return DecorationSet.create(tr.doc,decorations);
 }},props:{decorations:state=>proposalMarkKey.getState(state)},
 })];}});
