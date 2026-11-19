/** Keep WebGL aligned with the inherited DSH palette without rebuilding the view. */
export function bindCanvasTheme(container,onColor,{document=globalThis.document,getComputedStyle=e=>globalThis.getComputedStyle(e),MutationObserver=globalThis.MutationObserver,matchMedia=q=>globalThis.matchMedia?.(q)}={}){
  let closed=false,current;
  const update=()=>{
    if(closed)return;
    const value=getComputedStyle(container).backgroundColor.match(/^rgba?\(([\d.,\s/]+)\)$/)?.[1];
    const parts=value?.split(/[,\s/]+/).filter(Boolean).map(Number);
    if(!parts||parts.length<3||parts.length>4||parts.some(v=>!Number.isFinite(v))||parts.slice(0,3).some(v=>v<0||v>255)||parts[3]===0)return;
    const [r,g,b]=parts.map(Math.round),color=(r<<16)|(g<<8)|b;
    if(color!==current){current=color;onColor(color);}
  };
  const observer=new MutationObserver(update);
  for(const root of [document.documentElement,document.body])observer.observe(root,{attributes:true,attributeFilter:['data-ds-dark-theme','class','style']});
  const media=matchMedia?.('(prefers-color-scheme: dark)');media?.addEventListener('change',update);update();
  return ()=>{closed=true;observer.disconnect();media?.removeEventListener('change',update);};
}
