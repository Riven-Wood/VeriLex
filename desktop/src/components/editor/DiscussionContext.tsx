import {createContext,useCallback,useContext,useLayoutEffect,useRef,useState,type ReactNode} from 'react';
import type {JSONContent} from '@tiptap/core';
import type {EditorProposal,EditableProduct} from './client';
import type {EditorSelection} from './LitigationDocumentEditor';
export interface EditorTurnContext {product_id:string;base_hash:string;document:JSONContent;selection:EditorSelection|null;operation?:'generate';reference_path?:string}
interface Target {flush?:()=>Promise<void>;locate?:(selection?:{from?:number;to?:number;text?:string}|null)=>void;path?:string;id:string;title:string;kind:string;capture:()=>EditorTurnContext|null;clearSelection:()=>void;apply:(product:EditableProduct)=>void|Promise<void>}
interface Discussion {flush:()=>Promise<void>;applying:boolean;setApplying:(value:boolean)=>void;preview:EditorProposal|null;setPreview:(proposal:EditorProposal|null)=>void;target:Target|null;selection:EditorSelection|null;reference:string|null;setTarget:(target:Target|null)=>void;register:(target:Target)=>void;unregister:(id:string)=>void;renameIdentity:(id:string,title:string,path:string)=>void;activate:(id:string)=>void;setSelection:(selection:EditorSelection|null)=>void;setReference:(path:string|null)=>void;capture:()=>EditorTurnContext|null;sent:()=>void;threadId:string|null}
const Context=createContext<Discussion|null>(null);
export const useEditorDiscussion=()=>useContext(Context);
export function OrdinaryDiscussion({enabled,children}:{enabled:boolean;children:ReactNode}) {
 const discussion=useEditorDiscussion();
 return <Context.Provider value={enabled?null:discussion}>{children}</Context.Provider>;
}
export function EditorDiscussionProvider({threadId,workspaceId,children}:{threadId:string|null;workspaceId:string;children:ReactNode}) {
 const [applying,setApplying]=useState(false);
 const [preview,setPreview]=useState<EditorProposal|null>(null);
 const [target,setTarget]=useState<Target|null>(null),[selection,setSelection]=useState<EditorSelection|null>(null),[reference,setReference]=useState<string|null>(null);
 const latest=useRef({target,reference});latest.current={target,reference};
 const previous=useRef(threadId),threadRef=useRef(threadId),saved=useRef(new Map<string,Target|null>()),targets=useRef(new Map<string,Target>());
 threadRef.current=threadId;
 const key=(thread:string|null)=>`verilex:editor-discussion:${workspaceId}:${thread??'new'}`;
 const select=useCallback((value:Target|null)=>{setTarget(value);setSelection(null);setPreview(null);saved.current.set(threadRef.current??'new',value);if(value)localStorage.setItem(`verilex:editor-discussion:${workspaceId}:${threadRef.current??'new'}`,value.id);else localStorage.removeItem(`verilex:editor-discussion:${workspaceId}:${threadRef.current??'new'}`);},[workspaceId]);
 const renameIdentity=useCallback((id:string,title:string,path:string)=>{
  const value=targets.current.get(id);if(value)targets.current.set(id,{...value,title,path});
  for(const [thread,target] of saved.current)if(target?.id===id)saved.current.set(thread,{...target,title,path});
  if(latest.current.target?.id===id){const target={...latest.current.target,title,path};latest.current={...latest.current,target};setTarget(target);}
 },[]);
 const unregister=useCallback((id:string)=>{
  targets.current.delete(id);
  for(const [thread,value] of saved.current)if(value?.id===id)saved.current.set(thread,null);
  const prefix=`verilex:editor-discussion:${workspaceId}:`;
  for(const key of Object.keys(localStorage))if(key.startsWith(prefix)&&localStorage.getItem(key)===id)localStorage.removeItem(key);
  if(latest.current.target?.id===id){latest.current={...latest.current,target:null};setTarget(null);setSelection(null);setPreview(null);}
 },[workspaceId]);
 const activate=useCallback((id:string)=>{const value=targets.current.get(id);if(value)select(value);},[select]);
 const register=useCallback((value:Target)=>{targets.current.set(value.id,value);if(localStorage.getItem(`verilex:editor-discussion:${workspaceId}:${threadRef.current??'new'}`)===value.id)setTarget(value);},[workspaceId]);
 useLayoutEffect(()=>{if(previous.current===threadId)return;
   saved.current.set(previous.current??'new',latest.current.target);
   const next=previous.current===null&&threadId ? latest.current.target : saved.current.get(threadId??'new')??targets.current.get(localStorage.getItem(key(threadId))??'')??null;
   if(threadId===null){saved.current.delete('new');localStorage.removeItem(key(null));}
   else if(previous.current===null&&next)localStorage.setItem(key(threadId),next.id);
   setTarget(threadId===null?null:next);
   if(previous.current!==null||threadId===null){setSelection(null);next?.clearSelection();}
   setPreview(null);previous.current=threadId;
 },[threadId]);
 const capture=useCallback(()=>{const state=latest.current,value=state.target?.capture();return value?structuredClone({...value,...(state.reference&&state.reference!==state.target?.path?{reference_path:state.reference}:{})}):null;},[]);
 const sent=useCallback(()=>{latest.current.target?.clearSelection();setSelection(null);},[]);
 return <Context.Provider value={{flush:async()=>{for(const value of targets.current.values())await value.flush?.();},applying,setApplying,preview,setPreview,target,selection,reference,setTarget:select,register,unregister,renameIdentity,activate,setSelection,setReference,capture,sent,threadId}}>{children}</Context.Provider>;
}

export interface LitigationMaterialSelection {workspaceId:string;paths:string[]}
export const LitigationMaterialsContext=createContext<LitigationMaterialSelection|null>(null);
export const useLitigationMaterials=()=>useContext(LitigationMaterialsContext);
