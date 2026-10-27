import {invoke} from '@tauri-apps/api/core';

export function diagramFilename(title:string, extension:'svg'|'png'|'zip', page?:number):string {
  const stem=title.replace(/[\u0000-\u001f\u007f/\\:*?"<>|\u202a-\u202e\u2066-\u2069]/g,'').replace(/^\.+/,'').trim();
  return `${Array.from(stem||'诉讼图表').slice(0,50).join('')}${page?`-第${page}页`:''}.${extension}`;
}

export async function diagramPng(content:string):Promise<Uint8Array> {
  const image=new Image();
  image.src=`data:image/svg+xml;base64,${content}`;
  await new Promise<void>((resolve,reject)=>{image.onload=()=>resolve();image.onerror=()=>reject(new Error('图表无法转换为图片，请导出 SVG 原图。'));});
  const width=image.naturalWidth,height=image.naturalHeight;
  if(!width||!height||width>16384||height>16384||width*height>16_000_000)throw new Error('图表过大，无法完整转换为 PNG，请导出 SVG 原图。');
  const scale=Math.min(2,16384/width,16384/height,Math.sqrt(16_000_000/(width*height)));
  const canvas=document.createElement('canvas');canvas.width=Math.ceil(width*scale);canvas.height=Math.ceil(height*scale);
  const context=canvas.getContext('2d');if(!context)throw new Error('图片转换不可用，请导出 SVG 原图。');
  context.fillStyle='#fff';context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(image,0,0,canvas.width,canvas.height);
  const blob=await new Promise<Blob>((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(new Error('图片转换失败，请导出 SVG 原图。')),'image/png'));
  return new Uint8Array(await blob.arrayBuffer());
}

export async function saveDiagramExport(filename:string,data:Uint8Array):Promise<string|null> {
  if(data.length>25*1024*1024)throw new Error('图表超过25 MiB，请逐页导出。');
  if(Reflect.has(globalThis,'__TAURI_INTERNALS__'))return invoke<string|null>('save_litigation_diagram',{defaultName:filename,content:Array.from(data)});
  const type=filename.endsWith('.png')?'image/png':filename.endsWith('.zip')?'application/zip':'image/svg+xml';
  const url=URL.createObjectURL(new Blob([new Uint8Array(data)],{type}));const link=document.createElement('a');link.href=url;link.download=filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);return filename;
}
