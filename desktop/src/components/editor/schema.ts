import {ProposalMarks} from "./proposalMarks";
import {Extension, type JSONContent} from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import {TextStyle, FontFamily, FontSize} from '@tiptap/extension-text-style';
import TextAlign from '@tiptap/extension-text-align';
import {TableKit} from '@tiptap/extension-table';

const ParagraphFormat = Extension.create({
  name: 'paragraphFormat',
  addGlobalAttributes() {
    return [{types:['paragraph','heading'], attributes:{
      indent:{default:0,renderHTML:attrs=>attrs.indent?{style:`margin-left:${attrs.indent}pt`}:{}},
      lineHeight:{default:null,renderHTML:attrs=>attrs.lineHeight?{style:`line-height:${attrs.lineHeight}`}:{}}
    }}];
  },
});
export const editorExtensions = () => [
  StarterKit.configure({blockquote:false,code:false,codeBlock:false,horizontalRule:false,link:false,trailingNode:false}),
  TextStyle, FontFamily, FontSize, TextAlign.configure({types:['paragraph','heading']}),
  ProposalMarks, ParagraphFormat, TableKit.configure({table:{resizable:false}}),
];
export const blankDocument = (): JSONContent => ({type:'doc',content:[{type:'paragraph'}]});
