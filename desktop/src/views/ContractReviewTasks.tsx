import { useCallback, useEffect, useRef, useState } from "react";
import { Plus, X } from "lucide-react";
import { ContractReviewView, type ContractReviewViewProps, type ContractTaskSnapshot } from "./ContractReviewView";
import { wordDocumentApi } from "../runtime/wordDocumentClient";
import { contractReviewApi } from "../runtime/contractReviewClient";

const STORAGE_KEY = "verilex:contract-tasks:v1";
type StoredSnapshot = Omit<ContractTaskSnapshot, "document" | "detail"> & { documentId?: string; reviewId?: string };
type Task = { id: string; name: string; threadId: string | null; snapshot?: ContractTaskSnapshot; stored?: StoredSnapshot };
const blank = (): ContractTaskSnapshot => ({skill:"通用合同审查",party:"neutral",requirements:"",strictness:"normal",signature:localStorage.getItem("verilex:contract-signature:v1") ?? "VeriLex",retrieval:false,newReview:true,document:null,detail:null,threadId:null});
const newTask = ():Task => ({id:crypto.randomUUID(),name:"新建任务",threadId:null,snapshot:blank()});
function initialTasks(threadId: string | null): {tabs:Task[];active:string} {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    if (saved?.tabs?.length && saved.tabs.every((tab:Task) => typeof tab.id === "string" && typeof tab.name === "string" && tab.stored && typeof tab.stored.requirements === "string")) {
      return {tabs:saved.tabs,active:saved.tabs.some((tab:Task)=>tab.id===saved.active)?saved.active:saved.tabs[0].id};
    }
  } catch { /* Invalid UI preferences do not prevent opening local documents. */ }
  const task:Task = {id:crypto.randomUUID(),name:"新建任务",threadId};
  return {tabs:[task],active:task.id};
}
function stored(task:Task):Task {
  if (!task.snapshot) return task;
  const {document,detail,...settings}=task.snapshot;
  return {id:task.id,name:task.name,threadId:task.threadId,stored:{...settings,documentId:document?.id,reviewId:detail?.id}};
}

export function ContractReviewTasks(props:ContractReviewViewProps) {
  const [initial] = useState(()=>initialTasks(props.thread?.id ?? null));
  const [tabs,setTabs] = useState(initial.tabs);
  const [active,setActive] = useState(initial.active);
  const [ready,setReady] = useState<string|null>(null);
  const [switching,setSwitching] = useState(false);
  const [childBusy,setChildBusy] = useState(false);
  const [error,setError] = useState("");
  const guard = useRef<(()=>Promise<boolean>)|null>(null);
  const lock = useRef(false);
  const pendingSelection = useRef<string|null>(null);
  const pendingStart = useRef(false);
  const previousThread = useRef(props.thread?.id);
  const tabsRef=useRef(tabs);tabsRef.current=tabs;
  const current=tabs.find(tab=>tab.id===active)!;
  const busy=childBusy || ["running","awaiting_approval"].includes(props.thread?.status ?? "");
  const registerGuard=useCallback((next:(()=>Promise<boolean>)|null)=>{
    guard.current=next;props.onRegisterLeaveGuard?.(next);
  },[props.onRegisterLeaveGuard]);
  const capture=useCallback((snapshot:ContractTaskSnapshot)=>{
    setTabs(rows=>rows.map(tab=>tab.id===active?{...tab,snapshot,stored:undefined,threadId:snapshot.threadId,name:snapshot.document?.filename ?? snapshot.detail?.filename ?? "新建任务"}:tab));
  },[active]);
  useEffect(()=>{
    try {localStorage.setItem(STORAGE_KEY,JSON.stringify({active,tabs:tabs.map(stored)}));}
    catch {setError("任务列表未能保存到本机，合同文件仍按原方式保存。");}
  },[tabs,active]);
  const hydrate=async(task:Task):Promise<Task>=>{
    if (!task.stored) return task;
    const {documentId,reviewId,...settings}=task.stored;
    const [document,detail]=await Promise.all([documentId?wordDocumentApi.get(documentId):Promise.resolve(null),reviewId?contractReviewApi.get(reviewId):Promise.resolve(null)]);
    return {...task,stored:undefined,snapshot:{...settings,document,detail}};
  };
  const activate=async(task:Task)=>{
    const loaded=await hydrate(task);
    setTabs(rows=>rows.map(row=>row.id===loaded.id?loaded:row));
    if(loaded.threadId && loaded.threadId!==props.thread?.id){
      pendingSelection.current=loaded.id;
      setReady(null);
      props.onSelectReview(loaded.threadId);
      return;
    }
    setActive(loaded.id);setReady(loaded.id);
  };
  useEffect(()=>{
    let live=true;
    void hydrate(current).then(loaded=>{
      if(!live)return;
      setTabs(rows=>rows.map(row=>row.id===loaded.id?loaded:row));
      if(loaded.threadId&&loaded.threadId!==props.thread?.id){pendingSelection.current=loaded.id;setReady(null);props.onSelectReview(loaded.threadId);}
      else setReady(loaded.id);
    }).catch(()=>{if(live)setError("任务未能恢复。可以关闭此标签，再从审查历史重开；原文件没有删除。");});
    return()=>{live=false;};
    // Restore once; later task changes are explicit and save the current editor first.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[]);
  useEffect(()=>{
    const id=props.thread?.id;
    if(id===previousThread.current)return;
    previousThread.current=id;
    if(!id)return;
    if(pendingSelection.current){
      const chosen=tabsRef.current.find(tab=>tab.id===pendingSelection.current);
      if(chosen?.threadId===id){setActive(chosen.id);setReady(chosen.id);pendingSelection.current=null;setSwitching(false);lock.current=false;return;}
    }
    if(pendingStart.current){
      pendingStart.current=false;
      setTabs(rows=>rows.map(tab=>tab.id===active?{...tab,threadId:id}:tab));return;
    }
    const existing=tabsRef.current.find(tab=>tab.threadId===id);
    if(existing){void activate(existing).catch(()=>setError("任务未能打开，请从历史记录重试。"));}
    else {const task:Task={id:crypto.randomUUID(),name:props.thread?.title ?? "合同审查",threadId:id};setTabs(rows=>[...rows,task]);setActive(task.id);setReady(task.id);}
    // A global thread change is either a started review or explicit history navigation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[props.thread?.id]);
  const navigate=async(action:()=>Promise<void>|void)=>{
    if(lock.current||busy)return;
    lock.current=true;setSwitching(true);setError("");
    try {if(guard.current && !await guard.current())return;await action();}
    catch(cause){setError(cause instanceof Error?cause.message:"任务未能切换，请重试。");}
    finally {if(!pendingSelection.current){lock.current=false;setSwitching(false);}}
  };
  const add=()=>void navigate(()=>{
    const task=newTask();setTabs(rows=>[...rows,task]);setActive(task.id);setReady(task.id);
  });
  const close=(id:string)=>{
    if(busy||lock.current)return;
    const remove=async()=>{
      const remaining=tabsRef.current.filter(tab=>tab.id!==id);
      if(id!==active){setTabs(remaining);return;}
      const target=remaining[Math.min(tabsRef.current.findIndex(tab=>tab.id===id),remaining.length-1)] ?? newTask();
      const loaded=await hydrate(target);
      setTabs(remaining.length?remaining.map(tab=>tab.id===loaded.id?loaded:tab):[loaded]);
      if(loaded.threadId&&loaded.threadId!==props.thread?.id){pendingSelection.current=loaded.id;setReady(null);props.onSelectReview(loaded.threadId);}
      else {setActive(loaded.id);setReady(loaded.id);}
    };
    if(id===active)void navigate(remove);else void remove().catch(()=>setError("任务未能关闭，请重试。"));
  };
  const taskTabs=<div className="contract-task-tabs" role="tablist" aria-label="合同任务">
    {tabs.map(tab=><div className={`contract-task-tab${tab.id===active?" is-active":""}`} key={tab.id}>
      <button role="tab" aria-selected={tab.id===active} title={tab.name} disabled={switching||busy} onClick={()=>{if(tab.id!==active)void navigate(()=>activate(tab));}}>{tab.name}</button>
      <button aria-label={`关闭任务：${tab.name}`} title="关闭任务，保留文件" disabled={switching||busy} onClick={()=>close(tab.id)}><X size={14}/></button>
    </div>)}
    <button className="contract-task-add" aria-label="新建任务" title={busy?"请先停止当前审查，再新建任务":"新建任务"} disabled={switching||busy} onClick={add}><Plus size={17}/></button>
  </div>;
  return <div className="contract-task-workspace">
    {error&&<p role="alert" className="contract-review-error">{error}<button onClick={()=>setError("")}>关闭</button></p>}
    {ready===active ? <ContractReviewView key={active} {...props} initialSnapshot={current?.snapshot} onSnapshot={capture} taskTabs={taskTabs} onBusyChange={setChildBusy} onRegisterLeaveGuard={registerGuard}
      onSelectReview={threadId=>void navigate(async()=>{
        const existing=tabsRef.current.find(tab=>tab.threadId===threadId);
        if(existing) await activate(existing);
        else {
          const task:Task={id:crypto.randomUUID(),name:"合同审查",threadId};
          setTabs(rows=>[...rows,task]);pendingSelection.current=task.id;setReady(null);props.onSelectReview(threadId);
        }
      })}
      onStartReview={async(file,options)=>{pendingStart.current=true;try{await props.onStartReview(file,options);}catch(cause){pendingStart.current=false;throw cause;}}}/>
      : <div className="contract-task-loading">{taskTabs}<p role="status">正在打开任务…</p></div>}
  </div>;
}
