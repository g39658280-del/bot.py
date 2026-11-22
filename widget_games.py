import html
import zlib
import inspect
import aiohttp
from contextlib import suppress
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest
from cachetools import TTLCache

widget_games: TTLCache = TTLCache(maxsize=300, ttl=900)


def _minify_html(raw: str) -> str:
    """Сжимает лишние пробелы и переносы строк, чтобы уверенно уложиться в 4096 байт zlib."""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    return "\n".join(lines)


# ==========================================
# GEOMETRY DASH — ДЕТАЛИЗИРОВАННЫЙ ВИДЖЕТ
# ==========================================
GD_WIDGET_HTML = _minify_html(r"""
<!doctype html>
<meta charset="utf-8">
<style>
*{margin:0;padding:0;user-select:none;-webkit-user-select:none;touch-action:none}
body{background:#090d16;overflow:hidden;font:700 12px system-ui,sans-serif;color:#fff}
canvas{display:block;width:100%;height:220px}
</style>
<canvas id="c" width="340" height="220"></canvas>
<script>
const C=document.getElementById('c'),g=C.getContext('2d');
const W=340,H=220,FY=178,SZ=22,PX=64,GOAL=3150;
// Типы объектов: 0=шип, 1=блок(можно стоять сверху), 2=джамп-пад, 3=орб в воздухе
const LVL=[
[280,0,0],[440,0,0],[462,0,0],
[620,1,0],[644,1,0],[668,1,0],[668,0,24],
[820,2,0],[900,1,24],[924,1,24],[948,1,48],[972,1,48],[996,1,48],
[1140,0,0],[1162,0,0],[1184,0,0],
[1280,1,0],[1350,3,52],[1430,1,48],[1454,1,48],[1478,1,48],
[1560,0,0],[1582,0,0],[1660,2,0],[1750,1,48],[1774,1,48],[1798,0,72],
[1920,1,0],[1944,1,24],[1968,1,48],[2040,3,74],[2120,1,48],[2144,1,48],[2168,1,24],[2192,1,0],
[2320,0,0],[2342,0,0],[2440,2,0],[2510,3,76],[2590,1,72],[2614,1,72],[2638,1,72],
[2740,0,0],[2762,0,0],[2840,1,0],[2864,1,0],[2920,0,0],[2942,0,0]
];
let y=FY-SZ,vy=0,rot=0,cam=0,att=1,hold=0,tap=0,onG=1,dead=0,won=0,shake=0,jumps=0;
let parts=[],trail=[],usedOrbs=new Set();

C.onpointerdown=()=>{
  hold=1;tap=1;
  if(won){won=0;reset(1)}
};
C.onpointerup=C.onpointercancel=()=>{hold=0};

function reset(incAtt){
  cam=0;y=FY-SZ;vy=0;rot=0;onG=1;dead=0;trail=[];usedOrbs.clear();
  if(incAtt)att++;
}
function burst(bx,by,col,n){
  for(let i=0;i<n;i++){
    let a=Math.random()*6.283,s=1.5+Math.random()*4.5;
    parts.push({x:bx,y:by,vx:Math.cos(a)*s,vy:Math.sin(a)*s-1.5,l:24+Math.random()*14,s:3+Math.random()*4,c:col});
  }
}
function die(){
  if(dead)return;
  dead=30;shake=8;
  burst(PX+SZ/2,y+SZ/2,'#facc15',16);
  burst(PX+SZ/2,y+SZ/2,'#38bdf8',10);
}

function loop(){
  let pct=Math.min(100,Math.floor(cam/GOAL*100));
  let hue=(215+pct*1.4)%360;
  let bg=g.createLinearGradient(0,0,0,H);
  bg.addColorStop(0,`hsl(${hue},55%,12%)`);
  bg.addColorStop(1,`hsl(${(hue+30)%360},65%,20%)`);
  g.fillStyle=bg;g.fillRect(0,0,W,H);

  g.save();
  if(shake>0){g.translate((Math.random()-.5)*shake,(Math.random()-.5)*shake);shake*=.85}

  g.strokeStyle='rgba(255,255,255,0.06)';g.lineWidth=1;
  let gx=-(cam*.3)%40;
  for(let i=gx;i<W;i+=40){g.beginPath();g.moveTo(i,0);g.lineTo(i,FY);g.stroke()}
  for(let j=18;j<FY;j+=40){g.beginPath();g.moveTo(0,j);g.lineTo(W,j);g.stroke()}
  g.fillStyle='rgba(255,255,255,0.05)';
  for(let i=0;i<6;i++){
    let bx=((i*110-cam*.18)%660+660)%660-80;
    g.fillRect(bx,45+(i%3)*22,42,FY-45);
  }

  g.fillStyle=`hsl(${hue},60%,10%)`;g.fillRect(0,FY,W,H-FY);
  g.strokeStyle=`hsl(${hue},90%,60%)`;g.lineWidth=2;
  g.beginPath();g.moveTo(0,FY);g.lineTo(W,FY);g.stroke();
  let fx=-(cam%28);
  g.strokeStyle='rgba(255,255,255,0.12)';g.lineWidth=1;
  for(let i=fx;i<W;i+=28){g.strokeRect(i,FY+4,24,24)}

  if(!dead && !won){
    let prevY=y;
    cam+=4.1;
    if(hold && onG){vy=-8.8;onG=0;jumps++;burst(PX+4,y+SZ,'#38bdf8',4)}
    vy+=0.52;if(vy>12)vy=12;
    y+=vy;onG=0;
    if(y>=FY-SZ){y=FY-SZ;vy=0;onG=1}

    for(let idx=0;idx<LVL.length;idx++){
      let [ox,t,oh]=LVL[idx];
      let sx=ox-cam+PX,by=FY-24-oh;
      if(sx<-40||sx>W+40)continue;
      if(t===1){
        if(sx<PX+SZ-3 && sx+24>PX+3){
          if(prevY+SZ<=by+10 && vy>=0 && y+SZ>=by){
            y=by-SZ;vy=0;onG=1;
          }else if(y+SZ>by+4 && y<by+20){die()}
        }
      }else if(t===0){
        let sy=FY-oh;
        if(sx+5<PX+SZ-3 && sx+17>PX+3 && y+SZ>sy-16 && y<sy){die()}
      }else if(t===2){
        let sy=FY-oh;
        if(sx<PX+SZ && sx+24>PX && y+SZ>=sy-8 && y+SZ<=sy+6){
          vy=-11.2;onG=0;burst(sx+12,sy-4,'#facc15',8);
        }
      }else if(t===3){
        let cy=FY-oh-12;
        if(tap && !usedOrbs.has(idx) && Math.hypot(sx+12-(PX+SZ/2),cy-(y+SZ/2))<28){
          vy=-9.2;onG=0;usedOrbs.add(idx);burst(sx+12,cy,'#fde047',10);
        }
      }
    }
    if(onG)rot=Math.round(rot/90)*90;else rot+=6.5;
    trail.push({x:PX+6,y:y+SZ/2,a:.7});
    if(trail.length>14)trail.shift();
    if(cam>=GOAL){
      won=1;burst(PX+SZ/2,y+SZ/2,'#4ade80',25);
      try{TelegramWidget.request('gd_win')}catch(_){}
    }
  }else if(dead>0){
    dead--;if(dead===0)reset(1);
  }
  tap=0;

  for(let [ox,t,oh] of LVL){
    let sx=ox-cam+PX,by=FY-24-oh;
    if(sx<-32||sx>W+32)continue;
    if(t===1){
      g.fillStyle='#1e1b4b';g.fillRect(sx,by,24,24);
      g.strokeStyle='#818cf8';g.lineWidth=2;g.strokeRect(sx+1,by+1,22,22);
      g.fillStyle='rgba(129,140,248,0.22)';g.fillRect(sx+5,by+5,14,14);
    }else if(t===0){
      let sy=FY-oh;
      g.fillStyle='#0f172a';g.strokeStyle='#f43f5e';g.lineWidth=2;
      g.beginPath();g.moveTo(sx+1,sy);g.lineTo(sx+11,sy-21);g.lineTo(sx+21,sy);g.closePath();
      g.fill();g.stroke();
    }else if(t===2){
      let sy=FY-oh;
      g.fillStyle='#facc15';g.beginPath();g.arc(sx+12,sy,11,Math.PI,0);g.fill();
      g.fillStyle='#fef08a';g.fillRect(sx+4,sy-3,16,3);
    }else if(t===3){
      let cy=FY-oh-12;
      g.strokeStyle='#fde047';g.lineWidth=2.5;
      g.beginPath();g.arc(sx+12,cy,11,0,6.283);g.stroke();
      g.fillStyle='rgba(250,204,21,0.45)';g.beginPath();g.arc(sx+12,cy,7,0,6.283);g.fill();
    }
  }

  let fxP=GOAL-cam+PX;
  if(fxP>-20 && fxP<W+40){
    g.strokeStyle='#4ade80';g.lineWidth=4;
    g.beginPath();g.ellipse(fxP+12,FY-48,10,44,0,0,6.283);g.stroke();
  }

  for(let p of trail){
    p.x-=4.1;p.a*=.88;
    g.fillStyle=`rgba(56,189,248,${p.a})`;g.fillRect(p.x-3,p.y-3,6,6);
  }

  if(!dead){
    g.save();g.translate(PX+SZ/2,y+SZ/2);g.rotate(rot*Math.PI/180);
    g.fillStyle='#facc15';g.fillRect(-SZ/2,-SZ/2,SZ,SZ);
    g.strokeStyle='#090d16';g.lineWidth=2;g.strokeRect(-SZ/2,-SZ/2,SZ,SZ);
    g.fillStyle='#38bdf8';g.fillRect(-6,-6,12,12);
    g.fillStyle='#090d16';g.fillRect(-4,-4,3,3);g.fillRect(1,-4,3,3);g.fillRect(-4,2,8,2);
    g.restore();
  }

  for(let i=parts.length-1;i>=0;i--){
    let p=parts[i];p.x+=p.vx;p.y+=p.vy;p.vy+=.18;p.l--;
    g.fillStyle=p.c;g.globalAlpha=Math.max(0,p.l/30);
    g.fillRect(p.x,p.y,p.s,p.s);
    if(p.l<=0)parts.splice(i,1);
  }
  g.globalAlpha=1;g.restore();

  g.fillStyle='rgba(0,0,0,0.45)';g.fillRect(80,10,180,8);
  g.fillStyle='#4ade80';g.fillRect(80,10,1.8*pct,8);
  g.strokeStyle='rgba(255,255,255,0.4)';g.strokeRect(80,10,180,8);
  g.fillStyle='#fff';g.fillText(`ATT ${att}`,10,18);
  g.fillText(`${pct}%`,268,18);
  if(won){
    g.fillStyle='rgba(0,0,0,0.65)';g.fillRect(35,68,270,66);
    g.strokeStyle='#4ade80';g.lineWidth=2;g.strokeRect(35,68,270,66);
    g.fillStyle='#4ade80';g.font='700 16px system-ui';g.fillText('LEVEL COMPLETE!',95,94);
    g.fillStyle='#fff';g.font='700 12px system-ui';
    g.fillText(`Attempts: ${att} • Jumps: ${jumps} • Tap to replay`,52,118);
  }
  requestAnimationFrame(loop);
}
loop();
</script>
""")


# ==========================================
# DOOM — ДЕТАЛИЗИРОВАННЫЙ 2.5D RAYCASTER
# ==========================================
DOOM_WIDGET_HTML = _minify_html(r"""
<!doctype html>
<meta charset="utf-8">
<style>
*{margin:0;padding:0;box-sizing:border-box;user-select:none;-webkit-user-select:none;touch-action:none}
body{background:#09090b;color:#fff;font:700 11px monospace;overflow:hidden}
#v{display:block;width:100%;height:184px;image-rendering:pixelated}
.ctrl{display:grid;grid-template-columns:1fr 1fr 1fr 1fr 1.4fr;gap:4px;padding:4px;height:56px;background:#18181b;border-top:2px solid #3f3f46}
button{background:#27272a;color:#e4e4e7;border:1px solid #52525b;border-radius:6px;font:700 16px system-ui}
button:active{background:#52525b}
#fire{background:#991b1b;border-color:#ef4444;color:#fff;font-size:14px}
#fire:active{background:#dc2626}
</style>
<canvas id="v" width="160" height="100"></canvas>
<div class="ctrl">
  <button id="l">◀</button>
  <button id="u">▲</button>
  <button id="d">▼</button>
  <button id="r">▶</button>
  <button id="fire">🔥 FIRE</button>
</div>
<script>
const C=document.getElementById('v'),g=C.getContext('2d');
const W=160,H=82,HUD=18;
const img=g.createImageData(W,H),buf=img.data;

const MAP=[
"111122221111",
"100000000001",
"102200003301",
"102000000301",
"200004400002",
"200004400002",
"103000000201",
"103300002201",
"100000000001",
"111122221111"
];
const MW=12,MH=10;

const TEX=[];
for(let t=1;t<=4;t++){
  let arr=new Uint8Array(16*16*3);
  for(let y=0;y<16;y++)for(let x=0;x<16;x++){
    let i=(y*16+x)*3,r=90,gr=90,b=90;
    if(t===1){
      let m=(y%4===0)||((x+(y>>2)*4)%8===0);
      let n=((x*37+y*17)&15)-8;
      r=gr=b=m?42:118+n;
    }else if(t===2){
      let rim=(x===0||x===15||y===0||y===15);
      let panel=(x>3&&x<12&&y>3&&y<12);
      r=rim?45:panel?60:85;gr=rim?50:panel?110:92;b=rim?65:panel?175:110;
    }else if(t===3){
      let m=(y%4===0)||((x+(y>>2)*3)%6===0);
      r=m?55:155+((x*13+y*7)&15);gr=m?18:38;b=m?18:34;
    }else{
      let stripe=((x+y)&3)===0;
      r=stripe?40:55;gr=stripe?155:115;b=45;
    }
    arr[i]=r;arr[i+1]=gr;arr[i+2]=b;
  }
  TEX[t]=arr;
}

let px=1.5,py=1.5,pa=0.2,hp=100,ammo=24,kills=0,gunBob=0,recoil=0,dmgFlash=0,won=0,dead=0,tick=0;
let keys={};
let ents=[];
function initGame(){
  px=1.5;py=1.5;pa=0.2;hp=100;ammo=24;kills=0;recoil=0;dmgFlash=0;won=0;dead=0;
  ents=[
    {x:5.5,y:1.5,t:'imp',hp:3,hit:0,cd:50},
    {x:9.5,y:1.8,t:'caco',hp:4,hit:0,cd:70},
    {x:10.2,y:5.0,t:'imp',hp:3,hit:0,cd:60},
    {x:6.5,y:8.2,t:'caco',hp:4,hit:0,cd:65},
    {x:2.5,y:8.2,t:'imp',hp:3,hit:0,cd:55},
    {x:1.5,y:5.0,t:'med',hp:1},
    {x:8.5,y:5.0,t:'amo',hp:1}
  ];
}
initGame();
const TOTAL=5;

['l','u','d','r'].forEach(id=>{
  const b=document.getElementById(id);
  b.onpointerdown=()=>keys[id]=1;
  b.onpointerup=b.onpointercancel=()=>keys[id]=0;
});

function wallAt(x,y){
  let mx=Math.floor(x),my=Math.floor(y);
  if(my<0||my>=MH||mx<0||mx>=MW)return 1;
  return parseInt(MAP[my][mx]);
}

function hasLOS(x1,y1,x2,y2){
  let dx=x2-x1,dy=y2-y1,d=Math.hypot(dx,dy),st=Math.ceil(d*5);
  for(let i=1;i<st;i++)if(wallAt(x1+dx*i/st,y1+dy*i/st))return 0;
  return 1;
}

document.getElementById('fire').onpointerdown=()=>{
  if(dead||won){initGame();return}
  if(recoil>0||ammo<=0)return;
  ammo--;recoil=9;
  let best=null,bestD=99;
  for(let e of ents){
    if(e.hp<=0||(e.t!=='imp'&&e.t!=='caco'))continue;
    let dx=e.x-px,dy=e.y-py,dist=Math.hypot(dx,dy);
    let ang=Math.atan2(dy,dx)-pa;
    while(ang>Math.PI)ang-=6.283;while(ang<-Math.PI)ang+=6.283;
    if(Math.abs(ang)<0.22 && dist<8 && dist<bestD && hasLOS(px,py,e.x,e.y)){
      best=e;bestD=dist;
    }
  }
  if(best){
    best.hp--;best.hit=6;
    if(best.hp<=0){
      kills++;
      if(kills>=TOTAL){won=1;try{TelegramWidget.request('doom_win')}catch(_){}}
    }
  }
};

function loop(){
  tick++;
  if(!dead && !won){
    if(keys.l)pa-=0.065;
    if(keys.r)pa+=0.065;
    let mv=(keys.u?1:0)-(keys.d?1:0);
    if(mv){
      let nx=px+Math.cos(pa)*0.075*mv,ny=py+Math.sin(pa)*0.075*mv;
      if(!wallAt(nx+Math.sign(nx-px)*0.22,py))px=nx;
      if(!wallAt(px,ny+Math.sign(ny-py)*0.22))py=ny;
      gunBob+=0.28;
    }
    for(let e of ents){
      if(e.hp<=0)continue;
      let dx=px-e.x,dy=py-e.y,d=Math.hypot(dx,dy);
      if(e.t==='med'&&d<0.65&&hp<100){hp=Math.min(100,hp+35);e.hp=0;continue}
      if(e.t==='amo'&&d<0.65){ammo+=12;e.hp=0;continue}
      if(e.t==='imp'||e.t==='caco'){
        if(e.hit>0)e.hit--;
        if(d<6.5 && hasLOS(e.x,e.y,px,py)){
          if(d>1.2){
            let ex=e.x+dx/d*0.018,ey=e.y+dy/d*0.018;
            if(!wallAt(ex,e.y))e.x=ex;
            if(!wallAt(e.x,ey))e.y=ey;
          }
          e.cd--;
          if(e.cd<=0){
            e.cd=e.t==='caco'?58:72;
            hp=Math.max(0,hp-(e.t==='caco'?11:8));
            dmgFlash=7;
            if(hp<=0)dead=1;
          }
        }
      }
    }
  }
  if(recoil>0)recoil--;
  if(dmgFlash>0)dmgFlash--;

  for(let y=0;y<H;y++){
    let isF=y>=H/2;
    let d=isF?(y-H/2)/(H/2):(H/2-y)/(H/2);
    let r=isF?24+d*46:18+d*22,gr=isF?22+d*38:16+d*16,b=isF?24+d*34:22+d*20;
    for(let x=0;x<W;x++){
      let i=(y*W+x)*4;buf[i]=r;buf[i+1]=gr;buf[i+2]=b;buf[i+3]=255;
    }
  }

  let dirX=Math.cos(pa),dirY=Math.sin(pa),plX=-dirY*0.66,plY=dirX*0.66;
  let zbuf=new Float32Array(W);
  for(let x=0;x<W;x++){
    let camX=2*x/W-1,rx=dirX+plX*camX,ry=dirY+plY*camX;
    let mx=Math.floor(px),my=Math.floor(py);
    let ddx=Math.abs(1/(rx||1e-6)),ddy=Math.abs(1/(ry||1e-6));
    let stX=rx<0?-1:1,stY=ry<0?-1:1;
    let sdx=rx<0?(px-mx)*ddx:(mx+1-px)*ddx;
    let sdy=ry<0?(py-my)*ddy:(my+1-py)*ddy;
    let hit=0,side=0;
    for(let k=0;k<22&&!hit;k++){
      if(sdx<sdy){sdx+=ddx;mx+=stX;side=0}else{sdy+=ddy;my+=stY;side=1}
      hit=wallAt(mx,my);
    }
    let dist=Math.max(0.15,side===0?sdx-ddx:sdy-ddy);
    zbuf[x]=dist;
    let lh=Math.floor(H/dist),y0=Math.max(0,((H-lh)>>1)),y1=Math.min(H-1,((H+lh)>>1));
    let wx=side===0?py+dist*ry:px+dist*rx;wx-=Math.floor(wx);
    let tx=Math.floor(wx*16)&15,tex=TEX[hit]||TEX[1];
    let shade=(side?0.68:1)*Math.max(0.22,1-dist/11);
    for(let y=y0;y<=y1;y++){
      let ty=Math.floor(((y-(H-lh)/2)/lh)*16)&15;
      let ti=(ty*16+tx)*3,bi=(y*W+x)*4;
      buf[bi]=tex[ti]*shade;buf[bi+1]=tex[ti+1]*shade;buf[bi+2]=tex[ti+2]*shade;
    }
  }
  g.putImageData(img,0,0);

  let list=ents.filter(e=>e.hp>0).map(e=>({e,d:Math.hypot(e.x-px,e.y-py)})).sort((a,b)=>b.d-a.d);
  for(let {e,d} of list){
    let dx=e.x-px,dy=e.y-py,inv=1/(plX*dirY-dirX*plY);
    let tx=inv*(dirY*dx-dirX*dy),ty=inv*(-plY*dx+plX*dy);
    if(ty<=0.25)continue;
    let sx=Math.floor(W/2*(1+tx/ty)),sz=Math.min(76,Math.floor(H/ty));
    let col=Math.max(0,Math.min(W-1,sx));
    if(zbuf[col]<ty)continue;
    let yBase=H/2+sz/2;
    if(e.t==='imp'){
      g.fillStyle=e.hit?'#fff':'#92400e';g.fillRect(sx-sz*.26,yBase-sz*.88,sz*.52,sz*.82);
      g.fillStyle='#ef4444';g.fillRect(sx-sz*.14,yBase-sz*.74,sz*.1,sz*.1);g.fillRect(sx+sz*.04,yBase-sz*.74,sz*.1,sz*.1);
      g.fillStyle='#fbbf24';g.fillRect(sx-sz*.1,yBase-sz*.56,sz*.2,sz*.08);
    }else if(e.t==='caco'){
      g.fillStyle=e.hit?'#fff':'#dc2626';
      g.beginPath();g.arc(sx,H/2-sz*.08,sz*.34,0,6.283);g.fill();
      g.fillStyle='#22c55e';g.beginPath();g.arc(sx,H/2-sz*.14,sz*.12,0,6.283);g.fill();
      g.fillStyle='#1e1b4b';g.fillRect(sx-sz*.18,H/2+sz*.04,sz*.36,sz*.1);
    }else if(e.t==='med'){
      g.fillStyle='#f8fafc';g.fillRect(sx-sz*.2,yBase-sz*.32,sz*.4,sz*.32);
      g.fillStyle='#dc2626';g.fillRect(sx-sz*.05,yBase-sz*.26,sz*.1,sz*.2);g.fillRect(sx-sz*.12,yBase-sz*.2,sz*.24,sz*.08);
    }else if(e.t==='amo'){
      g.fillStyle='#ca8a04';g.fillRect(sx-sz*.2,yBase-sz*.26,sz*.4,sz*.26);
      g.fillStyle='#fef08a';g.fillRect(sx-sz*.14,yBase-sz*.2,sz*.28,sz*.06);
    }
  }

  let bx=W/2+Math.cos(gunBob)*3,by=H-24+Math.abs(Math.sin(gunBob))*3+recoil*1.8;
  if(recoil>5){
    g.fillStyle='#fef08a';g.beginPath();g.arc(bx,by-4,11,0,6.283);g.fill();
    g.fillStyle='#f97316';g.beginPath();g.arc(bx,by-4,6,0,6.283);g.fill();
  }
  g.fillStyle='#3f3f46';g.fillRect(bx-7,by,5,24);g.fillRect(bx+2,by,5,24);
  g.fillStyle='#71717a';g.fillRect(bx-8,by+12,16,14);
  g.fillStyle='rgba(255,255,255,0.6)';g.fillRect(W/2-3,H/2,7,1);g.fillRect(W/2,H/2-3,1,7);

  for(let my=0;my<MH;my++)for(let mx=0;mx<MW;mx++){
    g.fillStyle=MAP[my][mx]!=='0'?'rgba(255,255,255,0.35)':'rgba(0,0,0,0.4)';
    g.fillRect(W-MW*3-3+mx*3,3+my*3,3,3);
  }
  g.fillStyle='#4ade80';g.fillRect(W-MW*3-3+px*3-1,3+py*3-1,2,2);

  if(dmgFlash>0){g.fillStyle='rgba(220,38,38,0.38)';g.fillRect(0,0,W,H)}

  g.fillStyle='#3f3f46';g.fillRect(0,H,W,HUD);
  g.strokeStyle='#71717a';g.strokeRect(0.5,H+0.5,W-1,HUD-1);
  g.fillStyle='#ef4444';g.font='700 8px monospace';
  g.fillText(`AMMO`,6,H+7);g.fillText(`HP`,42,H+7);g.fillText(`KILLS`,116,H+7);
  g.fillStyle='#facc15';g.font='700 9px monospace';
  g.fillText(String(ammo).padStart(2,'0'),8,H+16);
  g.fillText(`${hp}%`,38,H+16);
  g.fillText(`${kills}/${TOTAL}`,116,H+16);
  g.fillStyle='#d4a373';g.fillRect(72,H+2,16,14);
  g.fillStyle='#3f2e21';g.fillRect(72,H+2,16,4);
  let look=(tick>>5)%3-1;
  g.fillStyle='#111';g.fillRect(75+look,H+7,2,2);g.fillRect(82+look,H+7,2,2);
  g.fillStyle=hp<40?'#dc2626':'#7f1d1d';g.fillRect(76,H+12,8,2);

  if(dead||won){
    g.fillStyle='rgba(0,0,0,0.68)';g.fillRect(16,24,128,34);
    g.fillStyle=won?'#4ade80':'#ef4444';g.font='700 11px monospace';
    g.fillText(won?'STAGE CLEAR!':'YOU DIED',won?42:54,39);
    g.fillStyle='#fff';g.font='700 8px monospace';
    g.fillText('Tap FIRE to restart',34,51);
  }
  requestAnimationFrame(loop);
}
loop();
</script>
""")


# ==========================================
# ОТПРАВКА ВИДЖЕТА (aiogram + прямой HTTP фолбэк + диагностика)
# ==========================================
async def _send_widget_raw_http(bot: Bot, payload: dict) -> tuple[int | None, str | None]:
    """Прямой вызов Telegram Bot API sendWidget, если версия aiogram ещё не знает этот метод."""
    url = f"https://api.telegram.org/bot{bot.token}/sendWidget"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json(content_type=None)
                if data.get("ok"):
                    msg_id = data.get("result", {}).get("message_id")
                    return msg_id, None
                desc = data.get("description") or f"HTTP {resp.status}"
                return None, desc
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


async def _safe_send_widget(
    bot: Bot,
    safe_send_msg,
    chat_id: int,
    widget_html: str,
    caption: str,
    width: int,
    height: int,
    business_connection_id: str | None = None,
    owner_id: int | None = None,
) -> int | None:
    """Возвращает message_id отправленного виджета или None (с выводом точной причины в чат/ЛС)."""
    packed_len = len(zlib.compress(widget_html.encode("utf-8"), 9))
    print(f"📦 Widget packed size: {packed_len}B (raw: {len(widget_html.encode('utf-8'))}B)")

    async def _report(err_text: str):
        sent = await safe_send_msg(
            chat_id=chat_id,
            text=err_text,
            business_connection_id=business_connection_id,
        )
        if not sent and owner_id:
            with suppress(Exception):
                await bot.send_message(chat_id=owner_id, text=err_text, parse_mode="HTML")

    if packed_len > 4096:
        await _report(f"❌ Виджет превышает лимит 4096 байт после zlib: <b>{packed_len} Б</b>.")
        return None

    payload: dict = {
        "chat_id": chat_id,
        "html": widget_html,
        "caption": caption,
        "width": width,
        "height": height,
        "allow_queries": True,
    }
    if business_connection_id:
        payload["business_connection_id"] = business_connection_id

    if hasattr(bot, "send_widget"):
        try:
            sent = await bot.send_widget(**payload)
            return getattr(sent, "message_id", None)
        except TypeError as e:
            sig = str(inspect.signature(bot.send_widget))
            print(f"⚠️ send_widget TypeError: {e} | sig={sig} -> trying raw HTTP")
        except TelegramBadRequest as e:
            print(f"⚠️ send_widget TelegramBadRequest: {e}")
            await _report(f"⚠️ Telegram отклонил виджет: <code>{html.escape(str(e))}</code>")
            return None
        except Exception as e:
            print(f"❌ send_widget error ({type(e).__name__}): {e}")
            await _report(f"❌ Ошибка виджета (<code>{type(e).__name__}</code>): <code>{html.escape(str(e))}</code>")
            return None

    # Фолбэк: прямой запрос к Telegram Bot API
    msg_id, err = await _send_widget_raw_http(bot, payload)
    if err:
        print(f"⚠️ sendWidget HTTP error: {err}")
        await _report(f"⚠️ Telegram API <code>sendWidget</code>: <code>{html.escape(err)}</code>")
        return None
    return msg_id


def register_widget_games(
    dp: Dispatcher,
    bot: Bot,
    *,
    connections_cache: dict,
    connections_collection,
    handle_owner_activity,
    get_user_balance,
    update_user_balance,
    parse_optional_bet,
    safe_send_msg,
):
    """Подключает команды .гд / .дум и обработчик побед widget_query к твоему боту."""

    async def _launch_game(message: Message, game_type: str):
        conn_id = getattr(message, "business_connection_id", None)
        if conn_id:
            owner_data = connections_cache.get(conn_id) or await connections_collection.find_one(
                {"business_connection_id": conn_id}
            )
            if not owner_data or message.from_user.id != owner_data["user_id"]:
                return
            owner_id = owner_data["user_id"]
            await handle_owner_activity(owner_id, message.chat.id)
            with suppress(Exception):
                await bot.delete_business_messages(
                    business_connection_id=conn_id,
                    message_ids=[message.message_id],
                )
        else:
            owner_id = message.from_user.id

        bet = parse_optional_bet(message.text)
        if bet > 0:
            bal = await get_user_balance(owner_id)
            if bal < bet:
                await safe_send_msg(
                    chat_id=message.chat.id,
                    text=f"❌ Недостаточно монет! Нужно: <b>{bet}</b>, у вас: <b>{bal}</b>.",
                    business_connection_id=conn_id,
                )
                return
            await update_user_balance(owner_id, -bet)

        bet_note = f" · Ставка {bet}💰 (приз x3)" if bet > 0 else " · Награда за прохождение +25💰"
        if game_type == "gd":
            html_code = GD_WIDGET_HTML
            caption = f"🟦 Geometry Dash{bet_note}"
            width, height = 340, 220
        else:
            html_code = DOOM_WIDGET_HTML
            caption = f"🔫 DOOM 2.5D{bet_note}"
            width, height = 340, 240

        sent_msg_id = await _safe_send_widget(
            bot=bot,
            safe_send_msg=safe_send_msg,
            chat_id=message.chat.id,
            widget_html=html_code,
            caption=caption,
            width=width,
            height=height,
            business_connection_id=conn_id,
            owner_id=owner_id,
        )
        if sent_msg_id:
            widget_games[f"{message.chat.id}_{sent_msg_id}"] = {
                "owner_id": owner_id,
                "bet": bet,
                "rewarded": False,
                "conn_id": conn_id,
            }
        elif bet > 0:
            # Возвращаем ставку, если виджет не удалось отправить
            await update_user_balance(owner_id, bet)

    @dp.business_message(F.text.lower().regexp(r"^\.(гд|геометридеш|gd)(?:\s+\d+)?$"))
    @dp.message(F.text.lower().regexp(r"^\.(гд|геометридеш|gd)(?:\s+\d+)?$"))
    async def cmd_gd_widget(message: Message):
        await _launch_game(message, "gd")

    @dp.business_message(F.text.lower().regexp(r"^\.(дум|doom)(?:\s+\d+)?$"))
    @dp.message(F.text.lower().regexp(r"^\.(дум|doom)(?:\s+\d+)?$"))
    async def cmd_doom_widget(message: Message):
        await _launch_game(message, "doom")

    if hasattr(dp, "widget_query"):
        @dp.widget_query()
        async def on_widget_query(query):
            data = getattr(query, "data", "")
            msg = getattr(query, "message", None)
            if msg and data in ("gd_win", "doom_win"):
                g_key = f"{msg.chat.id}_{msg.message_id}"
                session = widget_games.get(g_key)
                if session and not session.get("rewarded"):
                    session["rewarded"] = True
                    bet = session.get("bet", 0)
                    win_amt = (bet * 3) if bet > 0 else 25
                    new_bal = await update_user_balance(query.from_user.id, win_amt)
                    game_name = "Geometry Dash" if data == "gd_win" else "DOOM"
                    with suppress(Exception):
                        await bot.send_message(
                            chat_id=msg.chat.id,
                            text=(
                                f"🏆 <b>{html.escape(query.from_user.first_name or 'Игрок')}</b> "
                                f"прошёл <b>{game_name}</b>! (+{win_amt} монет · Баланс: <b>{new_bal}</b>)"
                            ),
                            parse_mode="HTML",
                            business_connection_id=session.get("conn_id"),
                        )
            with suppress(Exception):
                await bot.answer_widget_query(widget_query_id=query.id, data="ok")
