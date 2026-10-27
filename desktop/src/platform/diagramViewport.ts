/** A display copy only: original SVG files, hashes and export bytes stay intact. */
export interface DiagramViewport {content:string;width:number;height:number;minFont:number}
// The renderer uses textLength/lengthAdjust to justify long, wrapped text.
const attributes=new Set('xmlns viewBox width height x y x1 y1 x2 y2 cx cy r rx ry d points transform id fill fill-opacity fill-rule stroke stroke-width stroke-opacity stroke-linecap stroke-linejoin stroke-dasharray stroke-dashoffset opacity font-family font-size font-weight font-style text-anchor textLength lengthAdjust dominant-baseline letter-spacing dx dy marker-start marker-mid marker-end markerHeight markerWidth markerUnits orient refX refY preserveAspectRatio'.split(' '));
const SVG_NS='http://www.w3.org/2000/svg';
const allowed=new Set(['svg','g','defs','marker','path','rect','line','polyline','polygon','circle','ellipse','text','tspan','title','desc']);
function encoded(text:string) {
  const bytes=new TextEncoder().encode(text);let binary='';
  for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
  return btoa(binary);
}
export async function diagramViewport(content:string):Promise<DiagramViewport> {
  const bytes=Uint8Array.from(atob(content),c=>c.charCodeAt(0));
  const doc=new DOMParser().parseFromString(new TextDecoder().decode(bytes),'image/svg+xml');
  const source=doc.documentElement;
  if(doc.doctype||source.namespaceURI!==SVG_NS||source.localName!=='svg'||bytes.length>5_000_000)throw new Error('图表格式无效，无法显示。');
  for(const node of [source,...source.querySelectorAll('*')]) {
    if(node.namespaceURI!==SVG_NS||!allowed.has(node.localName))throw new Error('图表含有不支持的内容。');
    for(const attr of Array.from(node.attributes)) {
      if((!attributes.has(attr.name)&&!attr.name.startsWith('data-'))||attr.value.includes('\\')||/^on/i.test(attr.localName)||attr.localName==='href'||(/url\s*\(/i.test(attr.value)&&!/^url\(#[\w-]+\)$/.test(attr.value)))throw new Error('图表含有不支持的内容。');
    }
  }
  const svg=document.importNode(source,true) as unknown as SVGSVGElement;
  const host=document.createElement('div');host.style.cssText='position:fixed;left:-100000px;top:0;visibility:hidden;pointer-events:none';host.setAttribute('aria-hidden','true');
  host.attachShadow({mode:'closed'}).append(svg);document.body.append(host);
  try {
    await document.fonts?.ready;
    const vb=svg.viewBox.baseVal;
    if(!(vb.width>0&&vb.height>0))throw new Error('图表尺寸无效。');
    svg.setAttribute('width',String(vb.width));svg.setAttribute('height',String(vb.height));
    // Ignore only the full-page white background, never content boxes or table borders.
    for(const node of Array.from(svg.children)) {
      if(node.localName==='rect'&&!node.getAttribute('transform')&&!node.hasAttribute('stroke')&&/^#(?:fff|ffffff)$/i.test(node.getAttribute('fill')||'')) {
        const rect=node as SVGRectElement;
        if(rect.x.baseVal.value<=vb.x&&rect.y.baseVal.value<=vb.y&&rect.width.baseVal.value>=vb.width&&rect.height.baseVal.value>=vb.height)node.remove();
      }
    }
    const fonts=Array.from(svg.querySelectorAll('text')).map(t=>parseFloat(getComputedStyle(t).fontSize)).filter(n=>n>0);
    const minFont=fonts.length?Math.min(...fonts):16;
    const rootMatrix=svg.getCTM();if(!rootMatrix)throw new Error('图表无法排版。');
    const inverse=rootMatrix.inverse();
    const items=Array.from(svg.children).filter((n):n is SVGGraphicsElement=>typeof (n as SVGGraphicsElement).getBBox==='function'&&!['defs','marker'].includes(n.localName)).map(node=>{
      const b=node.getBBox(),m=node.getCTM();if(!m)return null;
      const matrix=inverse.multiply(m);
      const points=[[b.x,b.y],[b.x+b.width,b.y],[b.x,b.y+b.height],[b.x+b.width,b.y+b.height]].map(([x,y])=>new DOMPoint(x,y).matrixTransform(matrix));
      return {node,top:Math.min(...points.map(p=>p.y)),bottom:Math.max(...points.map(p=>p.y))};
    }).filter((v):v is NonNullable<typeof v>=>!!v);
    // Reduce completely empty horizontal bands (for example a print footer gap).
    // Any connector, table border or group crossing a band prevents its removal.
    const bands:{top:number;bottom:number}[]=[];
    for(const item of [...items].sort((a,b)=>a.top-b.top)) {
      const previous=bands.at(-1);
      if(previous&&item.top<=previous.bottom)previous.bottom=Math.max(previous.bottom,item.bottom);
      else bands.push({top:item.top,bottom:item.bottom});
    }
    const spacing=Math.max(24,minFont*1.5);
    const gaps=bands.slice(1).map((band,i)=>({end:band.top,remove:Math.max(0,band.top-bands[i].bottom-spacing)})).filter(g=>g.remove>0);
    for(const item of items) {
      const shift=gaps.filter(g=>item.top>=g.end-.01).reduce((sum,g)=>sum+g.remove,0);
      if(shift>0){const group=document.createElementNS(SVG_NS,'g');group.setAttribute('transform',`translate(0 ${-shift})`);svg.insertBefore(group,item.node);group.append(item.node);}
    }
    const b=svg.getBBox();
    if(!Number.isFinite(b.width)||b.width<=0||b.height<=0)throw new Error('图表没有可显示的内容。');
    const padding=Math.max(12,minFont*.5),width=b.width+padding*2,height=b.height+padding*2;
    svg.setAttribute('viewBox',`${b.x-padding} ${b.y-padding} ${width} ${height}`);
    svg.setAttribute('width',String(width));svg.setAttribute('height',String(height));
    return {content:encoded(new XMLSerializer().serializeToString(svg)),width,height,minFont};
  } finally {host.remove();}
}
