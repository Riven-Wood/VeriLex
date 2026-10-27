import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import {afterEach, expect, test, vi} from 'vitest';
import {KnowledgeCreateMenu} from '../src/components/KnowledgeCreateMenu';
afterEach(cleanup);
test('external selection opens ima only when selected, and Escape restores focus',()=>{
 const associate=vi.fn(), ima=vi.fn();render(<KnowledgeCreateMenu busy={false} onAssociate={associate} onIma={ima}/>);
 const trigger=screen.getByRole('button',{name:'建立知识库'});fireEvent.click(trigger);
 fireEvent.click(screen.getByRole('button',{name:'接入外部工具'}));expect(ima).not.toHaveBeenCalled();expect(associate).not.toHaveBeenCalled();
 fireEvent.keyDown(screen.getByRole('button',{name:'Obsidian'}),{key:'Escape'});expect(screen.queryByRole('group')).toBeNull();expect(document.activeElement).toBe(trigger);
 fireEvent.click(trigger);fireEvent.click(screen.getByRole('button',{name:'接入外部工具'}));fireEvent.click(screen.getByRole('button',{name:'ima'}));expect(ima).toHaveBeenCalledOnce();expect(associate).not.toHaveBeenCalled();expect(screen.queryByRole('group')).toBeNull();
});
test('clicking outside dismisses without starting a connection',()=>{
 const ima=vi.fn();render(<KnowledgeCreateMenu busy={false} onAssociate={vi.fn()} onIma={ima}/>);
 fireEvent.click(screen.getByRole('button',{name:'建立知识库'}));fireEvent.pointerDown(document.body);expect(screen.queryByRole('group')).toBeNull();expect(ima).not.toHaveBeenCalled();
});
