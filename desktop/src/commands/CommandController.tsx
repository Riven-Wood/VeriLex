import {useLayoutEffect,useRef,useState} from 'react';
import {bindCommands,matchCommand,type BoundCommand,type CommandHandlers} from './registry';
import {CommandPalette} from './CommandPalette';
import {GlobalShortcutEffect} from './GlobalShortcut';
export function CommandController({handlers,open,onOpen,onClose}:{handlers:CommandHandlers;open:boolean;onOpen:()=>void;onClose:()=>void}) {
  const commands=bindCommands({...handlers,commands:onOpen});
  const latest=useRef(commands);latest.current=commands;
  const busy=useRef(false);
  const [error,setError]=useState('');
  const run=async(command:BoundCommand)=>{
    if(busy.current)return;
    busy.current=true;setError('');
    onClose();
    // The dialog restores focus on unmount before a command focuses its target.
    await new Promise<void>(resolve=>requestAnimationFrame(()=>resolve()));
    try{await command.run();}catch{setError('未能完成该操作，请重试。');}finally{busy.current=false;}
  };
  const runRef=useRef(run);runRef.current=run;
  useLayoutEffect(()=>{
    const onKey=(event:KeyboardEvent)=>{const command=matchCommand(event,latest.current);if(command){event.preventDefault();void runRef.current(command);}};
    window.addEventListener('keydown',onKey);return()=>window.removeEventListener('keydown',onKey);
  },[]);
  return <><GlobalShortcutEffect/>{open&&<CommandPalette commands={commands.filter(command=>command.id!=='commands')} onClose={onClose} onRun={c=>void run(c)}/>}{error&&<div role="alert" className="command-error">{error}<button onClick={()=>setError('')}>关闭</button></div>}</>;
}
