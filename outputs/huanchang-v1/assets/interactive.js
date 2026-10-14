
const stage=document.querySelector('.stage'),palettes=[['#75a9f6','#d1b9ee','#ffa844'],['#91a994','#dce7b2','#ddb978'],['#7481a8','#bb99b5','#ff9e74']],names=['晴空','苔与光','暮色'];let palette=0,order=0;
function color(index){palette=index;palettes[index].forEach((v,i)=>stage.style.setProperty(['--blue','--pink','--sun'][i],v));document.querySelectorAll('[data-palette]').forEach(b=>{b.classList.toggle('selected',+b.dataset.palette===index);b.setAttribute('aria-pressed',String(+b.dataset.palette===index))});status()}
function arrange(n){order=n;document.querySelector('.one').style.transform=n%2?'translateX(75%) rotate(15deg)':'rotate(-15deg)';document.querySelector('.two').style.transform=n%2?'translateX(-60%) rotate(-30deg)':'rotate(35deg)';document.querySelector('.three').style.transform=n%2?'translate(-200%,130%)':'none';status()}
function status(){document.querySelector('#status').textContent=names[palette]+' · '+(order%2?'新排列':'原始排列')}
document.querySelectorAll('[data-palette]').forEach(b=>b.onclick=()=>color(+b.dataset.palette));document.querySelector('#arrange').onclick=()=>arrange(order+1);document.querySelector('#reset').onclick=()=>{color(0);arrange(0)};
