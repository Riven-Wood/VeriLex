// Local evidence adapter around the bundled MIT QuillPDF core. No MCP/model required.
import {readFileSync, writeFileSync, lstatSync} from 'node:fs';
import {resolve, basename} from 'node:path';
import {PDFDocument} from 'pdf-lib';
import {merge, extractPages, pageCount} from './core.ts';
const safe = name => {
  if (typeof name !== 'string' || basename(name) !== name || name.startsWith('.')) throw Error('Invalid job file');
  const path=resolve(process.cwd(),name);
  if (!lstatSync(path).isFile() || lstatSync(path).isSymbolicLink()) throw Error('Invalid job input');
  return path;
};
async function main(){
try {
  const job=JSON.parse(readFileSync(0,'utf8'));
  if(job.operation==='image') {
    const bytes=readFileSync(safe(job.input));
    const doc=await PDFDocument.create();
    const img=job.input.endsWith('.png')?await doc.embedPng(bytes):await doc.embedJpg(bytes);
    if(img.width*img.height>80000000) throw Error('Image too large');
    const landscape=img.width>img.height;
    const [width,height]=landscape?[842,595]:[595,842];
    const scale=Math.min((width-36)/img.width,(height-36)/img.height);
    const page=doc.addPage([width,height]);
    page.drawImage(img,{x:(width-img.width*scale)/2,y:(height-img.height*scale)/2,width:img.width*scale,height:img.height*scale});
    writeFileSync('result.pdf',await doc.save(),{flag:'wx'});
  } else if(job.operation==='merge') {
    if(!Array.isArray(job.inputs)||!job.inputs.length||job.inputs.length>200)throw Error('Invalid inputs');
    const parts=[];
    for(const input of job.inputs){
      const bytes=readFileSync(safe(input.name));
      const count=await pageCount(bytes);
      if(input.first<1||input.last>count||input.first>input.last)throw Error('Invalid page range');
      parts.push(await extractPages(bytes,Array.from({length:input.last-input.first+1},(_,i)=>String(input.first+i)).join(',')));
    }
    writeFileSync('result.pdf',parts.length===1?parts[0]:await merge(parts),{flag:'wx'});
  } else throw Error('Invalid operation');
  process.stdout.write('ok');
} catch {process.stderr.write('PDF processing failed');process.exitCode=1;}

}
void main();
