import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import {afterEach, expect, test, vi} from 'vitest';
import {ProjectRelationshipGraph, type ProjectRelationEntry} from '../src/views/ProjectRelationshipGraph';

afterEach(cleanup);

test('shared entities form one network and every relation keeps its details and source',()=>{
  const source={path:'合同.txt',page:null,quote:'双方签订合同。'};
  const entries:ProjectRelationEntry[]=[
    {title:'甲公司',other:'乙公司',detail:'采购合同',date:'2024-01-10',pending:[],sources:[source]},
    {title:'乙公司',other:'甲公司',detail:'支付关系',pending:['付款用途'],sources:[]},
    {title:'乙公司',other:'丙公司',detail:'保证关系',pending:[],sources:[]},
    {title:'甲公司',other:'',detail:'待确认的联系',pending:[],sources:[]},
  ];
  const open=vi.fn();
  const {container}=render(<ProjectRelationshipGraph entries={entries} onOpenSource={open}/>);
  expect(container.querySelectorAll('.project-relations-node')).toHaveLength(3);
  expect(container.querySelectorAll('.project-relations-edge')).toHaveLength(3);
  const labels=Array.from(container.querySelectorAll('.project-relations-edge circle')).map(element=>[element.getAttribute('cx'),element.getAttribute('cy')]);
  expect(labels[0]).not.toEqual(labels[1]);
  expect(screen.getByText('待确认的联系')).toBeTruthy();
  expect(screen.getByText('2024-01-10')).toBeTruthy();
  expect(screen.getByText('待核实：付款用途')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'出处：合同.txt'}));
  expect(open).toHaveBeenCalledWith(source);
  const node=container.querySelector('[aria-label="主体 甲公司"]')!;
  fireEvent.keyDown(node,{key:'Enter'});
  expect(node.getAttribute('aria-pressed')).toBe('true');
  expect(container.querySelectorAll('.project-relations-list li[data-dimmed="true"]')).toHaveLength(1);
  fireEvent.keyDown(node,{key:' '});
  expect(node.getAttribute('aria-pressed')).toBe('false');
});

test('a large network retains all relations and sources in a readable list',()=>{
  const entries=Array.from({length:41},(_,i)=>({title:`主体${i}`,other:'共同主体',detail:`关系${i}`,pending:[],sources:[]}));
  const {container}=render(<ProjectRelationshipGraph entries={entries}/>);
  expect(screen.getByText('关系较多，请查看下方完整清单。')).toBeTruthy();
  expect(container.querySelectorAll('.project-relations-list li')).toHaveLength(41);
  expect(screen.getByText('关系40')).toBeTruthy();
});
