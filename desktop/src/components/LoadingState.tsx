import {LoaderCircle} from 'lucide-react';
import type {ReactNode} from 'react';
import './LoadingState.css';

export function LoadingState({children, inline=false, action}:{children:ReactNode; inline?:boolean; action?:ReactNode}) {
  return <div className={`loading-state${inline ? ' loading-state-inline' : ''}`} role="status">
    <LoaderCircle className="loading-state-icon" size={24} aria-hidden="true"/>
    <span>{children}</span>
    {action}
  </div>;
}
