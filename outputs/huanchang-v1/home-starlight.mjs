import {ShaderMount} from './vendor/paper/shader-mount.js';

const fragment=`#version 300 es
precision highp float;
uniform mediump vec2 u_resolution;
uniform float u_time;
uniform sampler2D u_mask;
uniform float u_strength;
uniform vec2 u_size;
uniform vec4 u_band;
out vec4 fragColor;
float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float noise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(hash(i),hash(i+vec2(1.,0.)),f.x),mix(hash(i+vec2(0.,1.)),hash(i+vec2(1.,1.)),f.x),f.y);}
float fbm(vec2 p){return .58*noise(p)+.28*noise(p*2.03+4.2)+.14*noise(p*4.01+9.3);}
float mask(vec2 uv){return texture(u_mask,clamp(uv,vec2(0.),vec2(1.))).a;}
void main(){
 vec2 uv=gl_FragCoord.xy/u_resolution;uv.y=1.-uv.y;
 vec2 px=uv*u_size;float t=u_time;
 vec3 paper=vec3(.9804,.9765,.9608),ink=vec3(.10,.19,.15),emerald=vec3(.12,.45,.32),plum=vec3(.43,.27,.54),sage=vec3(.65,.75,.48);
 float veil=exp(-pow((px.y-(u_band.y+u_band.w*.5))/(u_band.w*.75),2.));
 float leftFade=smoothstep(0.,25.,px.x)*smoothstep(0.,25.,u_size.x-px.x);
 float edgeFade=leftFade*smoothstep(0.,20.,px.y)*smoothstep(0.,20.,u_size.y-px.y);
 vec3 bg=paper;float a=mask(uv);vec3 color=ink;
  float beam=pow(.5+.5*sin(px.x*.013-px.y*.01-t*.45),5.);
  color=mix(ink,emerald,.5);color=mix(color,plum,beam*.55);
  float light=exp(-pow((px.x-(u_band.x+u_band.z*(.5+.38*sin(t*.21))))/160.,2.));
  bg=mix(bg,vec3(.85,.89,.79),light*veil*.33*edgeFade*u_strength);
 fragColor=vec4(mix(bg,color,clamp(a,0.,1.)),1.);
}`;


// Accepted V5 free-cloud motion, fixed at standard strength (1).
let resumeFrame=8000;
export function mountStarlight(root){
 const copy=root.querySelector('.home-intro-copy'),host=copy.querySelector('.home-starlight-material');
 const canvas=copy.querySelector('.home-starlight-particles'),ctx=canvas.getContext('2d');
 const maskCanvas=document.createElement('canvas'),maskCtx=maskCanvas.getContext('2d');
 const preference=matchMedia('(prefers-reduced-motion: reduce)');
 const forceStatic=new URLSearchParams(location.search).get('motion')==='off';
 let mount,particles=[],alive=true,visible=true,frame,timer,generation=0,lastState=0;
 const random=n=>{const x=Math.sin(n*127.1+311.7)*43758.5453;return x-Math.floor(x)};
 const noise=(time,seed)=>{const n=Math.floor(time),f=time-n,s=f*f*f*(f*(f*6-15)+10);return random(n*17.23+seed)*(1-s)+random((n+1)*17.23+seed)*s};
 const position=(p,t)=>{
  t*=.36;
  const gate=noise(t*(.12+p.q*.14)+p.r*37,p.seed+120);
  const g=Math.max(0,Math.min(1,(gate-.12)/.76)),spread=2+g*g*(3-2*g)*(48+p.v*41);
  const angle=noise(t*(.20+p.v*.16)+p.q*29,p.seed+237)*Math.PI*4+t*(.14+p.r*.12);
  const curl=noise(t*.34+p.r*43,p.seed+715)-.5;
  return {x:p.x+Math.cos(angle)*spread+curl*spread*.6,y:p.y+Math.sin(angle)*spread*.78+Math.sin(t*.61+p.q*9)*spread*.2};
 };
 const reduced=()=>preference.matches||forceStatic;
 const running=()=>alive&&!!mount&&!reduced()&&visible&&!document.hidden;
 function draw(){
  frame=null;if(!alive)return;
  ctx.clearRect(0,0,canvas.width/2,canvas.height/2);
  if(reduced()||!mount)return;
  const t=mount.getCurrentFrame()/1000;
  for(const p of particles){
   const {x,y}=position(p,t),twinkle=.5+.5*Math.sin(t*.65*(1.1+p.r)+p.q*15);
   const alpha=.26+.53*twinkle,color=p.q>.64?'108,71,135':p.q>.27?'47,108,73':'129,150,83';
   ctx.shadowBlur=p.v>.88?5:0;ctx.shadowColor=`rgba(${color},.3)`;
   ctx.fillStyle=`rgba(${color},${alpha})`;ctx.beginPath();ctx.arc(x,y,.5+p.v*.75+twinkle*.3,0,Math.PI*2);ctx.fill();
   if(p.r>.92){
    const prev=position(p,t-.22);ctx.shadowBlur=0;ctx.lineWidth=.55;
    ctx.strokeStyle=`rgba(${color},${alpha*.35})`;ctx.beginPath();ctx.moveTo(prev.x,prev.y);ctx.lineTo(x,y);ctx.stroke();
    if(twinkle>.68){const r=2.3+p.v*2;ctx.strokeStyle=`rgba(${color},${alpha*.55})`;ctx.beginPath();ctx.moveTo(x-r,y);ctx.lineTo(x+r,y);ctx.moveTo(x,y-r);ctx.lineTo(x,y+r);ctx.stroke()}
   }
  }
  ctx.shadowBlur=0;
  if(t-lastState>.5){lastState=t;root.dataset.starFrame=String(Math.round(t*1000));if(particles.length){const p=position(particles[0],t);root.dataset.firstStar=`${p.x.toFixed(2)},${p.y.toFixed(2)}`}}
  if(running())frame=requestAnimationFrame(draw);
 }
 function sync(){
  const staticMode=reduced();root.dataset.motion=staticMode?'reduced':'full';
  root.classList.toggle('starlight-ready',!!mount&&!staticMode);
  root.dataset.starRunning=String(running());mount?.setSpeed(running()?1:0);
  cancelAnimationFrame(frame);draw();
 }
 async function build(){
  const token=++generation;await document.fonts.ready;
  if(!alive||!root.isConnected||reduced())return;
  const box=host.getBoundingClientRect();if(!box.width||!box.height)return;
  maskCanvas.width=Math.ceil(box.width*2);maskCanvas.height=Math.ceil(box.height*2);
  maskCtx.setTransform(2,0,0,2,0,0);maskCtx.fillStyle='#fff';maskCtx.textBaseline='alphabetic';
  for(const el of copy.querySelectorAll('.home-letter')){
   const style=getComputedStyle(el),rect=el.getBoundingClientRect(),size=parseFloat(style.fontSize);
   maskCtx.font=`${style.fontWeight} ${size}px ${style.fontFamily}`;
   const m=maskCtx.measureText(el.textContent),asc=m.fontBoundingBoxAscent||m.actualBoundingBoxAscent,desc=m.fontBoundingBoxDescent||m.actualBoundingBoxDescent;
   maskCtx.fillText(el.textContent,rect.left-box.left,rect.top-box.top+(rect.height-asc-desc)/2+asc);
  }
  const image=new Image();image.src=maskCanvas.toDataURL();await image.decode();
  if(token!==generation||!alive||!root.isConnected||reduced())return;
  const data=maskCtx.getImageData(0,0,maskCanvas.width,maskCanvas.height).data;particles=[];
  for(let y=0;y<maskCanvas.height;y+=10)for(let x=0;x<maskCanvas.width;x+=10){
   const seed=x*7+y*13;if(data[(y*maskCanvas.width+x)*4+3]>175&&random(seed+811)>=.20)particles.push({x:x/2,y:y/2,seed,r:random(seed),q:random(seed+90),v:random(seed+31)});
  }
  canvas.width=Math.round(box.width*2);canvas.height=Math.round(box.height*2);ctx.setTransform(2,0,0,2,0,0);
  const title=copy.querySelector('h1').getBoundingClientRect();
  const uniforms={u_mask:image,u_strength:1,u_size:[box.width,box.height],u_band:[title.left-box.left,title.top-box.top,title.width,title.height]};
  try{
   if(mount)mount.setUniforms(uniforms);
   else mount=new ShaderMount(host,fragment,uniforms,{alpha:false,antialias:false,preserveDrawingBuffer:true},0,resumeFrame,2,1800000);
   root.dataset.stars=String(particles.length);sync();
  }catch(error){mount?.dispose();mount=null;host.replaceChildren();sync();console.warn('首页星光使用静态文字预览',error)}
 }
 const resize=new ResizeObserver(()=>{clearTimeout(timer);timer=setTimeout(build,140)});resize.observe(copy);
 const visibility=new IntersectionObserver(entries=>{visible=entries[0]?.isIntersecting??false;sync()});visibility.observe(copy);
 const onVisibility=()=>sync(),onPreference=()=>{sync();if(!reduced()&&!mount)build()};
 document.addEventListener('visibilitychange',onVisibility);preference.addEventListener('change',onPreference);
 sync();build();
 function dispose(){if(!alive)return;resumeFrame=mount?.getCurrentFrame()??resumeFrame;alive=false;generation++;clearTimeout(timer);cancelAnimationFrame(frame);resize.disconnect();visibility.disconnect();document.removeEventListener('visibilitychange',onVisibility);preference.removeEventListener('change',onPreference);mount?.dispose();mount=null;root.classList.remove('starlight-ready');window.removeEventListener('pagehide',dispose)}
 window.addEventListener('pagehide',dispose,{once:true});return dispose;
}
