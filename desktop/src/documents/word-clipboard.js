/* Clipboard HTML is parsed into data, never mounted in the editing document.
 * The pinned DOCX engine owns insertion, formatting and transactional history. */
'use strict';
window.parseWordClipboard = function(html) {
  const unsupported=()=>{throw new Error('clipboard_format_unsupported');};
  if(typeof html!=='string'||html.length>1000000)unsupported();
  const doc=new DOMParser().parseFromString(html,'text/html');
  if(doc.querySelector('script,iframe,object,embed,svg,math,img,table,video,audio,canvas,input,textarea,select'))unsupported();
  const rules=[];
  for(const style of doc.querySelectorAll('style')){
    if(!style.textContent.trim())continue;
    // A constructed, unadopted sheet parses CSS without loading its resources.
    if(typeof CSSStyleSheet.prototype.replaceSync!=='function')unsupported();
    const sheet=new CSSStyleSheet();sheet.replaceSync(style.textContent);
    for(const rule of sheet.cssRules){
      if(!rule.selectorText){if(rule.cssText.startsWith('@font-face')||rule.cssText.startsWith('@page'))continue;unsupported();}
      for(const selector of rule.selectorText.split(',')){
        const value=selector.trim();
        // Office clipboard rules use simple element/class selectors. Complex
        // selectors cannot be flattened without reproducing the CSS cascade.
        if(!/^[\w.*#\-\s>]+$/.test(value))unsupported();
        const weight=(value.match(/#/g)||[]).length*100+(value.match(/\./g)||[]).length*10+(value.match(/(?:^|[\s>])[a-z]+/gi)||[]).length;
        rules.push({selector:value,style:rule.style,weight,index:rules.length});
      }
    }
  }
  rules.sort((a,b)=>a.weight-b.weight||a.index-b.index);
  function styles(node){
    const values={},priorities={};
    function take(style){
      const keys=new Set([...Array(style.length)].map((_,i)=>style[i]));
      for(const key of ['font-family','font-size','font-weight','font-style','margin-top','margin-bottom','margin-left','margin-right','background-color','line-height'])if(style.getPropertyValue(key))keys.add(key);
      for(const key of keys){const important=style.getPropertyPriority(key)==='important';if(priorities[key]&&!important)continue;values[key]=style.getPropertyValue(key);priorities[key]=important;}
    }
    for(const rule of rules)if(node.matches(rule.selector))take(rule.style);
    // DOMParser inherits the editor CSP, which can suppress style attributes.
    // Parse their declarations through CSSOM on an unattached element instead;
    // this neither mounts source HTML nor relaxes the editor's CSP.
    const declared=document.createElement('span').style;
    declared.cssText=node.getAttribute('style')||'';
    take(declared);return values;
  }
  function points(value,base=12){
    if(!value)return null;
    const match=String(value).trim().match(/^(-?[\d.]+)(pt|px|in|cm|mm|em|%)?$/i);if(!match)return null;
    const n=Number(match[1]),unit=match[2]?.toLowerCase();
    return n*({pt:1,px:.75,in:72,cm:72/2.54,mm:72/25.4,em:base,'%':base/100}[unit]??1);
  }
  function color(value){
    const style=document.createElement('span').style;style.color=value;
    const normalized=style.color;
    const hex=normalized.match(/^#([\da-f]{6})$/i);if(hex)return hex[1].toUpperCase();
    const rgb=normalized.match(/^rgb\(\s*(\d+),\s*(\d+),\s*(\d+)\s*\)$/);if(rgb)return rgb.slice(1).map(n=>Number(n).toString(16).padStart(2,'0')).join('').toUpperCase();
    const names={black:'000000',white:'FFFFFF',red:'FF0000',blue:'0000FF',green:'008000',gray:'808080',grey:'808080',yellow:'FFFF00'};
    if(names[normalized])return names[normalized];unsupported();
  }
  const paragraphs=[];let current=null;
  const defaults={bold:false,italic:false,underline:false,strike:false,vertAlign:'baseline',fontFamily:'',fontSizePts:0,color:'auto'};
  function paragraph(format={},list=null){current={runs:[],format,list};paragraphs.push(current);return current;}
  function append(text,format,pre){
    if(!pre)text=text.replace(/[\t\r\n ]+/g,' ');
    if(!current){if(!text.trim())return;paragraph();}
    const previous=current.runs.at(-1);
    if(!pre&&(!previous||/[ \n]$/.test(previous.text)))text=text.replace(/^ +/,'');
    if(!text)return;
    if(previous&&previous.preserveSpaces===pre&&JSON.stringify(previous.format)===JSON.stringify(format))previous.text+=text;
    else current.runs.push({text,format:{...format},preserveSpaces:pre});
  }
  function walk(node,inherited=defaults,pre=false,list=null,depth=0){
    if(depth>80||paragraphs.length>500)unsupported();
    if(node.nodeType===3){append(node.data,inherited,pre);return;}
    if(node.nodeType!==1)return;
    const tag=node.tagName.toLowerCase();
    if(['style','meta','link','title','head'].includes(tag))return;
    const css=styles(node),format={...inherited};
    if(css.display==='none'||css.visibility==='hidden')return;
    if(tag==='a'&&node.hasAttribute('href'))unsupported();
    if(['b','strong'].includes(tag))format.bold=true;
    if(/^h[1-6]$/.test(tag)){format.bold=true;format.fontSizePts={h1:24,h2:18,h3:14,h4:12,h5:10,h6:8}[tag];}
    if(['i','em'].includes(tag))format.italic=true;
    if(tag==='u')format.underline=true;
    if(['s','strike','del'].includes(tag))format.strike=true;
    if(tag==='sup'||tag==='sub')format.vertAlign=tag==='sup'?'superscript':'subscript';
    if(css['font-weight'])format.bold=css['font-weight']==='bold'||Number(css['font-weight'])>=600;
    if(css['font-style'])format.italic=['italic','oblique'].includes(css['font-style']);
    if(css['text-decoration']||css['text-decoration-line']){const value=css['text-decoration-line']||css['text-decoration'];format.underline=value.includes('underline');format.strike=value.includes('line-through');}
    const family=css['font-family']||node.getAttribute('face');if(family)format.fontFamily=family.split(',')[0].trim().replace(/^['"]|['"]$/g,'');
    const size=points(css['font-size'],inherited.fontSizePts||12);if(size!=null){if(size<1||size>200)unsupported();format.fontSizePts=Math.round(size*2)/2;}
    if(css.color||node.getAttribute('color'))format.color=color(css.color||node.getAttribute('color'));
    if(css['vertical-align']&&['super','sub','baseline'].includes(css['vertical-align']))format.vertAlign={super:'superscript',sub:'subscript',baseline:'baseline'}[css['vertical-align']];
    if(css['background-color']&&!['transparent','rgba(0, 0, 0, 0)'].includes(css['background-color']))unsupported();
    if(tag==='br'){append('\n',format,true);return;}
    pre=/pre/.test(css['white-space']||'')||pre||tag==='pre';
    if(tag==='ol'||tag==='ul'){if(list)unsupported();list=tag==='ol'?'decimal':'bullet';if(tag==='ol'&&node.hasAttribute('start')&&node.getAttribute('start')!=='1')unsupported();}
    const block=['p','div','li','h1','h2','h3','h4','h5','h6','pre','blockquote'].includes(tag);
    const hasBlocks=[...node.children].some(child=>['p','div','li','ul','ol','h1','h2','h3','h4','h5','h6','pre'].includes(child.tagName.toLowerCase()));
    if(block&&!hasBlocks){
      const para={alignment:'left',spacingBefore:0,spacingAfter:0,leftIndent:0,rightIndent:0,firstLineIndent:0,lineSpacing:240,lineSpacingRule:'auto'};
      const alignment=css['text-align']||node.getAttribute('align');if(['left','center','right','justify'].includes(alignment))para.alignment=alignment;
      for(const [key,target] of Object.entries({'margin-top':'spacingBefore','margin-bottom':'spacingAfter','margin-left':'leftIndent','margin-right':'rightIndent'})){const value=points(css[key],format.fontSizePts||12);if(value!=null)para[target]=Math.round(value*20);}
      const indent=points(css['text-indent'],format.fontSizePts||12);if(indent!=null){if(indent<0)delete para.firstLineIndent;para[indent<0?'hangingIndent':'firstLineIndent']=Math.round(Math.abs(indent)*20);}
      if(css['line-height']&&css['line-height']!=='normal'){
        if(/^[\d.]+$/.test(css['line-height'])){para.lineSpacing=Math.round(Number(css['line-height'])*240);para.lineSpacingRule='auto';}
        else {const line=points(css['line-height'],format.fontSizePts||12);if(line!=null){para.lineSpacing=Math.round(line*20);para.lineSpacingRule='exact';}}
      }
      paragraph(para,list);
    }else if(block)current=null;
    for(const child of node.childNodes)walk(child,format,pre,list,depth+1);
    if(block)current=null;
  }
  walk(doc.body);
  for(const para of paragraphs){
    if(para.runs.length===1&&para.runs[0].text==='\n')para.runs=[];
    const last=para.runs.at(-1);if(last&&!last.preserveSpaces)last.text=last.text.replace(/ +$/,'');
  }
  if(!paragraphs.length)unsupported();
  return {paragraphs};
};
