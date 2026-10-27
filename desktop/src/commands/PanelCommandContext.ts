import {createContext} from 'react';
export const PanelCommandContext=createContext<{panel:'files'|'terminal';sequence:number}|null>(null);
