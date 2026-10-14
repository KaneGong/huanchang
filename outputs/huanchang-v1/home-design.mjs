import {mountStarlight} from './home-starlight.mjs?v=20261013-6';
// Home presentation only; discovery, permissions and stored content remain in app.mjs.
const letters=word=>[...word].map(char=>`<span class="home-letter">${char}</span>`).join('');
const slogan=`<h1 id="home-title" aria-label="让想象成形，让彼此相遇。"><span class="home-line" aria-hidden="true">${letters('让想象')}<span class="home-emphasis" data-effect="form" data-word="成形">${letters('成形')}</span>${letters('，')}</span><span class="home-line" aria-hidden="true">${letters('让彼此')}<span class="home-emphasis" data-effect="meet" data-word="相遇">${letters('相遇')}</span>${letters('。')}</span></h1>`;
export function homeIntro(h,search){return `<section class="hc-intro home-intro" aria-labelledby="home-title"><div class="home-intro-copy"><div class="home-starlight-material" aria-hidden="true"></div><div class="home-starlight-layer" aria-hidden="true"><canvas class="home-starlight-particles"></canvas></div><p class="eyebrow"><span class="home-wordmark">HUANCHANG</span><span class="home-label-divider"></span>AI 创作与交流社区</p>${slogan}<p class="intro-subline">分享 AI 创作，发现心动的作品，<span>找到愿意一起做事的人。</span></p></div><form id="search-form" class="home-searchbar" role="search"><label class="sr-only" for="search">搜索作品、主题、方法或创作者</label><svg class="hc-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.6"/><path d="m16 16 4.4 4.4"/></svg><input id="search" name="query" type="search" value="${h(search)}" placeholder="搜索作品、主题、方法或创作者"><button type="submit">搜索<svg class="hc-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 12h15M13 5l7 7-7 7"/></svg></button></form></section>`}
let observer,animations=[],cleanupStars,firstVisit=true;
export function disposeHomeMotion(){observer?.disconnect();observer=null;cleanupStars?.();cleanupStars=null;for(const a of animations)a.cancel();animations=[]}
export function mountHomeMotion(root){
 disposeHomeMotion();if(!root?.matches('.is-home-front'))return;
 cleanupStars=mountStarlight(root);
 if(matchMedia('(prefers-reduced-motion: reduce)').matches||new URLSearchParams(location.search).get('motion')==='off')return;
 const enter=(element,delay=0)=>{if(typeof element?.animate==='function')animations.push(element.animate([{opacity:0,transform:'translateY(12px)'},{opacity:1,transform:'translateY(0)'}],{duration:520,delay,easing:'cubic-bezier(.22,1,.36,1)',fill:'backwards'}))};
 if(firstVisit){firstVisit=false;enter(root.querySelector('.home-searchbar'))}
 observer=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){enter(entry.target,Number(entry.target.dataset.homeRevealIndex||0)%3*55);observer.unobserve(entry.target)}},{threshold:.08});
 [...root.querySelectorAll('.theme-entry,.hc-wall>.hc-card,.hc-editorial')].forEach((e,i)=>{e.dataset.homeRevealIndex=i;observer.observe(e)});
}
