// Scoped MCP surface; QuillPDF core.ts is MIT licensed, see LICENSE.txt.
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { operate, userError } from './operations.mjs';
const root = process.cwd();
const server = new McpServer({name:'verilex-quillpdf', version:'1.0.0'});
const input = z.string().min(1).max(2048).describe('连接时选定文件夹内的 PDF 路径，可用相对路径');
const output_name = z.string().min(5).max(104).describe('新 PDF 文件名，不含目录。每次保存到独立的 PDF结果 文件夹，不覆盖原件');
const delivery = '仅本机页面处理，原件保留。结果会显示在同一工作区的会话文件栏，供用户预览。只报告实际输出；无需读取正文。';
const specs = [
 ['pdf_merge','合并 PDF', '按给定顺序合并 2 至 20 份 PDF。', {inputs:z.array(input).min(2).max(20),output_name}],
 ['pdf_split','拆分 PDF', '按页段拆为多份 PDF，每个逗号分隔的页段对应一份，例如 1-3,4-6。', {input,output_name,ranges:z.string().max(2000)}],
 ['pdf_extract','提取 PDF 页面', '按页码提取到一份 PDF，支持范围、重排和重复页，例如 5,1-3,1。', {input,output_name,pages:z.string().max(2000)}],
 ['pdf_page_count','查看 PDF 页数', '读取 PDF 总页数。', {input}],
];
for (const [name,title,description,inputSchema] of specs) server.registerTool(name, {
 title,description:description+delivery,inputSchema,
 annotations:{readOnlyHint:name==='pdf_page_count',destructiveHint:false,idempotentHint:name==='pdf_page_count',openWorldHint:false}
}, async args => {
 try { return {content:[{type:'text',text:JSON.stringify(await operate(root,name,args))}]}; }
 catch (err) { return {isError:true,content:[{type:'text',text:userError(err)}]}; }
});
server.connect(new StdioServerTransport()).catch(() => { process.exitCode = 1; });
