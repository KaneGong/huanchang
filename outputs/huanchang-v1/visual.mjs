// Presentation helpers: no account, persistence or permission behavior.
export function icon(name){
 const paths={bell:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9Z"/><path d="M10 21h4"/>',message:'<path d="M4 4h16v12H9l-5 4V4Z"/><path d="M8 8h8M8 12h5"/>',search:'<circle cx="10.8" cy="10.8" r="6.6"/><path d="m16 16 4.4 4.4"/>',arrow:'<path d="M4 12h15M13 5l7 7-7 7"/>',back:'<path d="M20 12H5M11 5l-7 7 7 7"/>',external:'<path d="M14 4h6v6M20 4 10 14M10 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/>',bookmark:'<path d="M6 4h12v17l-6-4-6 4V4Z"/>',heart:'<path d="M20.3 5.6c-2.2-2.2-5.6-1.8-8.3.9-2.7-2.7-6.1-3.1-8.3-.9-2.3 2.4-1.8 5.8.7 8.3L12 21l7.6-7.1c2.5-2.5 3-5.9.7-8.3Z"/>',share:'<path d="M12 15V3M7 8l5-5 5 5M5 13v7h14v-7"/>',play:'<path d="m9 5 11 7-11 7V5Z"/>',sound:'<path d="M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4"/>',plus:'<path d="M12 5v14M5 12h14"/>'};
 return `<svg class="hc-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]||paths.arrow}</svg>`;
}
export const themes=[
 {id:'world',title:'角色与世界',caption:'从一个角色，到它生活的世界',match:w=>/角色|世界观/.test((w.tags||[]).join(' '))},
 {id:'story',title:'让故事发生',caption:'文字、画面与声音的不同叙事',match:w=>['文字','视频','声音'].includes(w.form)},
 {id:'senses',title:'形态与感官',caption:'材质、自然与可以参与的空间',match:w=>['3D','互动'].includes(w.form)||/自然|材质|空间/.test((w.tags||[]).join(' '))}
];
export function themeFor(id){return themes.find(t=>t.id===id)}
export function imageRatio(w){if(w.cover?.id!==w.cover?.sourceId&&w.cover?.ratio)return w.cover.ratio;return w.form==='声音'?1:w.form==='3D'?.94:w.form==='文字'?1:w.form==='视频'?1.52:/角色\/IP/.test((w.tags||[])[0]||'')?1.10:1.32}
let wallObserver;
export function disposeWall(){wallObserver?.disconnect();wallObserver=null}
export function arrangeWall(wall){
 disposeWall();if(!wall?.isConnected)return;
 const place=entry=>{const card=entry.target;card.style.setProperty('--rows',Math.ceil((card.getBoundingClientRect().height+34)/8))};
 for(const card of wall.querySelectorAll('.hc-card'))place({target:card});
 if(typeof ResizeObserver==='undefined')return;
 wallObserver=new ResizeObserver(entries=>{for(const entry of entries)place(entry)});
 for(const card of wall.querySelectorAll('.hc-card'))wallObserver.observe(card);
}
