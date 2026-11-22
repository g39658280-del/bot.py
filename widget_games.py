import os
import hmac
import hashlib
import html
from contextlib import suppress
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from cachetools import TTLCache

# Активные игровые сессии (чтобы нельзя было начислить монеты дважды за одну игру)
widget_games: TTLCache = TTLCache(maxsize=500, ttl=1800)
_cached_bot_username: str | None = None


def _get_public_url() -> str:
    """Render сам прокидывает RENDER_EXTERNAL_URL (например, https://mybot.onrender.com)."""
    url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("PUBLIC_URL") or ""
    return url.rstrip("/")


def _sign_session(bot_token: str, sid: str, uid: int) -> str:
    return hmac.new(bot_token.encode(), f"{sid}:{uid}".encode(), hashlib.sha256).hexdigest()[:16]


async def _get_bot_username(bot: Bot) -> str:
    global _cached_bot_username
    if not _cached_bot_username:
        me = await bot.get_me()
        _cached_bot_username = me.username or ""
    return _cached_bot_username


# ==========================================
# GEOMETRY DASH (Telegram Mini App HTML)
# ==========================================
GD_HTML = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<title>Geometry Dash</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box;user-select:none;-webkit-user-select:none;touch-action:none}
  html,body{width:100%;height:100%;background:#090d16;overflow:hidden;font:700 14px system-ui,sans-serif;color:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center}
  canvas{width:100%;max-width:520px;height:auto;aspect-ratio:340/220;border-radius:12px;border:2px solid #1e293b;box-shadow:0 10px 30px rgba(0,0,0,.6)}
  .hint{margin-top:12px;color:#94a3b8;font-size:13px;text-align:center}
</style>
</head>
<body>
<canvas id="c" width="340" height="220"></canvas>
<div class="hint">Тапай или удерживай экран, чтобы прыгать</div>
<script>
try{const tg=window.Telegram.WebApp;tg.ready();tg.expand()}catch(_){}
const QS=new URLSearchParams(location.search);
const SID=QS.get('sid')||'',UID=QS.get('uid')||'',SIG=QS.get('sig')||'';

const C=document.getElementById('c'),g=C.getContext('2d');
const W=340,H=220,FY=178,SZ=22,PX=64,GOAL=3150;
// 0=шип, 1=блок(можно стоять сверху), 2=джамп-пад, 3=орб в воздухе
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
let y=FY-SZ,vy=0,rot=0,cam=0,att=1,hold=0,tap=0,onG=1,dead=0,won=0,shake=0,jumps=0,sentWin=0;
let parts=[],trail=[],usedOrbs=new Set();

const down=()=>{hold=1;tap=1;if(won){won=0;reset(1)}};
const up=()=>{hold=0};
document.addEventListener('pointerdown',down);
document.addEventListener('pointerup',up);
document.addEventListener('pointercancel',up);
document.addEventListener('keydown',e=>{if(e.code==='Space'||e.code==='ArrowUp')down()});
document.addEventListener('keyup',e=>{if(e.code==='Space'||e.code==='ArrowUp')up()});

function reset(inc){cam=0;y=FY-SZ;vy=0;rot=0;onG=1;dead=0;trail=[];usedOrbs.clear();if(inc)att++}
function burst(bx,by,col,n){
  for(let i=0;i<n;i++){
    let a=Math.random()*6.283,s=1.5+Math.random()*4.5;
    parts.push({x:bx,y:by,vx:Math.cos(a)*s,vy:Math.sin(a)*s-1.5,l:24+Math.random()*14,s:3+Math.random()*4,c:col});
  }
}
function die(){if(dead)return;dead=30;shake=8;burst(PX+11,y+11,'#facc15',16);burst(PX+11,y+11,'#38bdf8',10)}
function claimWin(){
  if(sentWin||!SID)return;
  sentWin=1;
  fetch('/api/game_win',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sid:SID,uid:UID,sig:SIG,game:'gd'})}).catch(()=>{});
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
  for(let i=-(cam*.3)%40;i<W;i+=40){g.beginPath();g.moveTo(i,0);g.lineTo(i,FY);g.stroke()}
  for(let j=18;j<FY;j+=40){g.beginPath();g.moveTo(0,j);g.lineTo(W,j);g.stroke()}
  g.fillStyle='rgba(255,255,255,0.05)';
  for(let i=0;i<6;i++)g.fillRect(((i*110-cam*.18)%660+660)%660-80,45+(i%3)*22,42,FY-45);

  g.fillStyle=`hsl(${hue},60%,10%)`;g.fillRect(0,FY,W,H-FY);
  g.strokeStyle=`hsl(${hue},90%,60%)`;g.lineWidth=2;g.beginPath();g.moveTo(0,FY);g.lineTo(W,FY);g.stroke();
  g.strokeStyle='rgba(255,255,255,0.12)';g.lineWidth=1;
  for(let i=-(cam%28);i<W;i+=28)g.strokeRect(i,FY+4,24,24);

  if(!dead&&!won){
    let prevY=y;cam+=4.1;
    if(hold&&onG){vy=-8.8;onG=0;jumps++;burst(PX+4,y+SZ,'#38bdf8',4)}
    vy=Math.min(12,vy+0.52);y+=vy;onG=0;
    if(y>=FY-SZ){y=FY-SZ;vy=0;onG=1}
    for(let idx=0;idx<LVL.length;idx++){
      let [ox,t,oh]=LVL[idx],sx=ox-cam+PX,by=FY-24-oh;
      if(sx<-40||sx>W+40)continue;
      if(t===1&&sx<PX+SZ-3&&sx+24>PX+3){
        if(prevY+SZ<=by+10&&vy>=0&&y+SZ>=by){y=by-SZ;vy=0;onG=1}
        else if(y+SZ>by+4&&y<by+20)die();
      }else if(t===0&&sx+5<PX+SZ-3&&sx+17>PX+3&&y+SZ>FY-oh-16&&y<FY-oh){die()}
      else if(t===2&&sx<PX+SZ&&sx+24>PX&&y+SZ>=FY-oh-8&&y+SZ<=FY-oh+6){vy=-11.2;onG=0;burst(sx+12,FY-oh-4,'#facc15',8)}
      else if(t===3&&tap&&!usedOrbs.has(idx)&&Math.hypot(sx+12-(PX+11),FY-oh-12-(y+11))<28){vy=-9.2;onG=0;usedOrbs.add(idx);burst(sx+12,FY-oh-12,'#fde047',10)}
    }
    rot=onG?Math.round(rot/90)*90:rot+6.5;
    trail.push({x:PX+6,y:y+11,a:.7});if(trail.length>14)trail.shift();
    if(cam>=GOAL){won=1;burst(PX+11,y+11,'#4ade80',25);claimWin()}
  }else if(dead>0&&--dead===0){reset(1)}
  tap=0;

  for(let [ox,t,oh] of LVL){
    let sx=ox-cam+PX,by=FY-24-oh,sy=FY-oh;
    if(sx<-32||sx>W+32)continue;
    if(t===1){g.fillStyle='#1e1b4b';g.fillRect(sx,by,24,24);g.strokeStyle='#818cf8';g.lineWidth=2;g.strokeRect(sx+1,by+1,22,22);g.fillStyle='rgba(129,140,248,0.22)';g.fillRect(sx+5,by+5,14,14)}
    else if(t===0){g.fillStyle='#0f172a';g.strokeStyle='#f43f5e';g.lineWidth=2;g.beginPath();g.moveTo(sx+1,sy);g.lineTo(sx+11,sy-21);g.lineTo(sx+21,sy);g.closePath();g.fill();g.stroke()}
    else if(t===2){g.fillStyle='#facc15';g.beginPath();g.arc(sx+12,sy,11,Math.PI,0);g.fill();g.fillStyle='#fef08a';g.fillRect(sx+4,sy-3,16,3)}
    else{g.strokeStyle='#fde047';g.lineWidth=2.5;g.beginPath();g.arc(sx+12,sy-12,11,0,6.283);g.stroke();g.fillStyle='rgba(250,204,21,0.45)';g.beginPath();g.arc(sx+12,sy-12,7,0,6.283);g.fill()}
  }

  let fxP=GOAL-cam+PX;
  if(fxP>-20&&fxP<W+40){g.strokeStyle='#4ade80';g.lineWidth=4;g.beginPath();g.ellipse(fxP+12,FY-48,10,44,0,0,6.283);g.stroke()}

  for(let p of trail){p.x-=4.1;p.a*=.88;g.fillStyle=`rgba(56,189,248,${p.a})`;g.fillRect(p.x-3,p.y-3,6,6)}

  if(!dead){
    g.save();g.translate(PX+11,y+11);g.rotate(rot*Math.PI/180);
    g.fillStyle='#facc15';g.fillRect(-11,-11,SZ,SZ);
    g.strokeStyle='#090d16';g.lineWidth=2;g.strokeRect(-11,-11,SZ,SZ);
    g.fillStyle='#38bdf8';g.fillRect(-6,-6,12,12);
    g.fillStyle='#090d16';g.fillRect(-4,-4,3,3);g.fillRect(1,-4,3,3);g.fillRect(-4,2,8,2);
    g.restore();
  }

  for(let i=parts.length-1;i>=0;i--){
    let p=parts[i];p.x+=p.vx;p.y+=p.vy;p.vy+=.18;p.l--;
    g.fillStyle=p.c;g.globalAlpha=Math.max(0,p.l/30);g.fillRect(p.x,p.y,p.s,p.s);
    if(p.l<=0)parts.splice(i,1);
  }
  g.globalAlpha=1;g.restore();

  g.fillStyle='rgba(0,0,0,0.45)';g.fillRect(80,10,180,8);
  g.fillStyle='#4ade80';g.fillRect(80,10,1.8*pct,8);
  g.strokeStyle='rgba(255,255,255,0.4)';g.strokeRect(80,10,180,8);
  g.fillStyle='#fff';g.font='700 12px system-ui';g.fillText(`ATT ${att}`,10,18);g.fillText(`${pct}%`,268,18);

  if(won){
    g.fillStyle='rgba(0,0,0,0.72)';g.fillRect(35,68,270,66);
    g.strokeStyle='#4ade80';g.lineWidth=2;g.strokeRect(35,68,270,66);
    g.fillStyle='#4ade80';g.font='700 16px system-ui';g.fillText('LEVEL COMPLETE!',95,94);
    g.fillStyle='#fff';g.font='700 12px system-ui';g.fillText(`Att: ${att} • Jumps: ${jumps} • Tap to replay`,56,118);
  }
  requestAnimationFrame(loop);
}
loop();
</script>
</body>
</html>"""


# ==========================================
# DOOM 2.5D RAYCASTER (Telegram Mini App HTML)
# ==========================================
DOOM_HTML = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<title>DOOM 2.5D</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box;user-select:none;-webkit-user-select:none;touch-action:none}
  html,body{width:100%;height:100%;background:#09090b;color:#fff;font:700 12px monospace;overflow:hidden;display:flex;flex-direction:column;justify-content:center;align-items:center}
  .wrap{width:100%;max-width:460px;display:flex;flex-direction:column;height:100%;max-height:620px}
  #v{width:100%;flex:1;image-rendering:pixelated;background:#000}
  .ctrl{display:grid;grid-template-columns:1fr 1fr 1fr 1fr 1.5fr;gap:6px;padding:10px;height:86px;background:#18181b;border-top:2px solid #3f3f46}
  button{background:#27272a;color:#e4e4e7;border:1px solid #52525b;border-radius:10px;font:700 22px system-ui}
  button:active{background:#52525b}
  #f{background:#991b1b;border-color:#ef4444;color:#fff;font-size:16px}
  #f:active{background:#dc2626}
</style>
</head>
<body>
<div class="wrap">
  <canvas id="v" width="160" height="100"></canvas>
  <div class="ctrl">
    <button id="l">◀</button>
    <button id="u">▲</button>
    <button id="d">▼</button>
    <button id="r">▶</button>
    <button id="f">🔥 FIRE</button>
  </div>
</div>
<script>
try{const tg=window.Telegram.WebApp;tg.ready();tg.expand()}catch(_){}
const QS=new URLSearchParams(location.search);
const SID=QS.get('sid')||'',UID=QS.get('uid')||'',SIG=QS.get('sig')||'';

const C=document.getElementById('v'),g=C.getContext('2d');
const W=160,H=82,HUD=18,img=g.createImageData(W,H),buf=img.data;
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
const MW=12,MH=10,TEX=[];
for(let t=1;t<=4;t++){
  let arr=new Uint8Array(768);
  for(let y=0;y<16;y++)for(let x=0;x<16;x++){
    let i=(y*16+x)*3,r=90,gr=90,b=90;
    if(t===1){let m=(y%4===0)||((x+(y>>2)*4)%8===0);r=gr=b=m?42:118+((x*37+y*17)&15)-8}
    else if(t===2){let rim=!x||x===15||!y||y===15,p=x>3&&x<12&&y>3&&y<12;r=rim?45:p?60:85;gr=rim?50:p?110:92;b=rim?65:p?175:110}
    else if(t===3){let m=(y%4===0)||((x+(y>>2)*3)%6===0);r=m?55:155+((x*13+y*7)&15);gr=m?18:38;b=m?18:34}
    else{let s=((x+y)&3)===0;r=s?40:55;gr=s?155:115;b=45}
    arr[i]=r;arr[i+1]=gr;arr[i+2]=b;
  }
  TEX[t]=arr;
}

let px,py,pa,hp,ammo,kills,gunBob=0,recoil,dmgFlash,won,dead,tick=0,sentWin=0,keys={},ents=[];
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
document.addEventListener('keydown',e=>{
  if(e.code==='ArrowLeft'||e.code==='KeyA')keys.l=1;
  if(e.code==='ArrowRight'||e.code==='KeyD')keys.r=1;
  if(e.code==='ArrowUp'||e.code==='KeyW')keys.u=1;
  if(e.code==='ArrowDown'||e.code==='KeyS')keys.d=1;
  if(e.code==='Space')shoot();
});
document.addEventListener('keyup',e=>{
  if(e.code==='ArrowLeft'||e.code==='KeyA')keys.l=0;
  if(e.code==='ArrowRight'||e.code==='KeyD')keys.r=0;
  if(e.code==='ArrowUp'||e.code==='KeyW')keys.u=0;
  if(e.code==='ArrowDown'||e.code==='KeyS')keys.d=0;
});

function wallAt(x,y){let mx=x|0,my=y|0;return my<0||my>=MH||mx<0||mx>=MW?1:+MAP[my][mx]}
function hasLOS(x1,y1,x2,y2){
  let dx=x2-x1,dy=y2-y1,n=Math.ceil(Math.hypot(dx,dy)*5);
  for(let i=1;i<n;i++)if(wallAt(x1+dx*i/n,y1+dy*i/n))return 0;
  return 1;
}
function claimWin(){
  if(sentWin||!SID)return;
  sentWin=1;
  fetch('/api/game_win',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sid:SID,uid:UID,sig:SIG,game:'doom'})}).catch(()=>{});
}
function shoot(){
  if(dead||won){initGame();return}
  if(recoil>0||ammo<=0)return;
  ammo--;recoil=9;
  let best=null,bestD=99;
  for(let e of ents){
    if(e.hp<=0||(e.t!=='imp'&&e.t!=='caco'))continue;
    let dx=e.x-px,dy=e.y-py,dist=Math.hypot(dx,dy);
    let ang=Math.atan2(dy,dx)-pa;
    while(ang>Math.PI)ang-=6.283;while(ang<-Math.PI)ang+=6.283;
    if(Math.abs(ang)<0.22&&dist<8&&dist<bestD&&hasLOS(px,py,e.x,e.y)){best=e;bestD=dist}
  }
  if(best){
    best.hp--;best.hit=6;
    if(best.hp<=0&&++kills>=TOTAL){won=1;claimWin()}
  }
}
document.getElementById('f').onpointerdown=shoot;

function loop(){
  tick++;
  if(!dead&&!won){
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
        if(d<6.5&&hasLOS(e.x,e.y,px,py)){
          if(d>1.2){
            let ex=e.x+dx/d*0.018,ey=e.y+dy/d*0.018;
            if(!wallAt(ex,e.y))e.x=ex;
            if(!wallAt(e.x,ey))e.y=ey;
          }
          if(--e.cd<=0){
            e.cd=e.t==='caco'?58:72;
            hp=Math.max(0,hp-(e.t==='caco'?11:8));
            dmgFlash=7;if(hp<=0)dead=1;
          }
        }
      }
    }
  }
  if(recoil>0)recoil--;
  if(dmgFlash>0)dmgFlash--;

  for(let y=0;y<H;y++){
    let isF=y>=41,d=isF?(y-41)/41:(41-y)/41;
    let r=isF?24+d*46:18+d*22,gr=isF?22+d*38:16+d*16,b=isF?24+d*34:22+d*20;
    for(let x=0;x<W;x++){let i=(y*W+x)*4;buf[i]=r;buf[i+1]=gr;buf[i+2]=b;buf[i+3]=255}
  }

  let dirX=Math.cos(pa),dirY=Math.sin(pa),plX=-dirY*0.66,plY=dirX*0.66,zbuf=new Float32Array(W);
  for(let x=0;x<W;x++){
    let camX=2*x/W-1,rx=dirX+plX*camX,ry=dirY+plY*camX,mx=px|0,my=py|0;
    let ddx=Math.abs(1/(rx||1e-6)),ddy=Math.abs(1/(ry||1e-6));
    let stX=rx<0?-1:1,stY=ry<0?-1:1;
    let sdx=rx<0?(px-mx)*ddx:(mx+1-px)*ddx,sdy=ry<0?(py-my)*ddy:(my+1-py)*ddy,hit=0,side=0;
    for(let k=0;k<22&&!hit;k++){
      if(sdx<sdy){sdx+=ddx;mx+=stX;side=0}else{sdy+=ddy;my+=stY;side=1}
      hit=wallAt(mx,my);
    }
    let dist=Math.max(0.15,side?sdy-ddy:sdx-ddx);zbuf[x]=dist;
    let lh=(H/dist)|0,y0=Math.max(0,(H-lh)>>1),y1=Math.min(H-1,(H+lh)>>1);
    let wx=side?px+dist*rx:py+dist*ry;wx-=wx|0;
    let tx=((wx*16)|0)&15,tex=TEX[hit]||TEX[1],shade=(side?0.68:1)*Math.max(0.22,1-dist/11);
    for(let y=y0;y<=y1;y++){
      let ty=((((y-(H-lh)/2)/lh)*16)|0)&15,ti=(ty*16+tx)*3,bi=(y*W+x)*4;
      buf[bi]=tex[ti]*shade;buf[bi+1]=tex[ti+1]*shade;buf[bi+2]=tex[ti+2]*shade;
    }
  }
  g.putImageData(img,0,0);

  let list=ents.filter(e=>e.hp>0).map(e=>({e,d:Math.hypot(e.x-px,e.y-py)})).sort((a,b)=>b.d-a.d);
  for(let {e} of list){
    let dx=e.x-px,dy=e.y-py,inv=1/(plX*dirY-dirX*plY);
    let tx=inv*(dirY*dx-dirX*dy),ty=inv*(-plY*dx+plX*dy);
    if(ty<=0.25)continue;
    let sx=(80*(1+tx/ty))|0,sz=Math.min(76,(H/ty)|0),col=Math.max(0,Math.min(W-1,sx));
    if(zbuf[col]<ty)continue;
    let yB=41+sz/2;
    if(e.t==='imp'){
      g.fillStyle=e.hit?'#fff':'#92400e';g.fillRect(sx-sz*.26,yB-sz*.88,sz*.52,sz*.82);
      g.fillStyle='#ef4444';g.fillRect(sx-sz*.14,yB-sz*.74,sz*.1,sz*.1);g.fillRect(sx+sz*.04,yB-sz*.74,sz*.1,sz*.1);
      g.fillStyle='#fbbf24';g.fillRect(sx-sz*.1,yB-sz*.56,sz*.2,sz*.08);
    }else if(e.t==='caco'){
      g.fillStyle=e.hit?'#fff':'#dc2626';g.beginPath();g.arc(sx,41-sz*.08,sz*.34,0,6.283);g.fill();
      g.fillStyle='#22c55e';g.beginPath();g.arc(sx,41-sz*.14,sz*.12,0,6.283);g.fill();
      g.fillStyle='#1e1b4b';g.fillRect(sx-sz*.18,41+sz*.04,sz*.36,sz*.1);
    }else if(e.t==='med'){
      g.fillStyle='#f8fafc';g.fillRect(sx-sz*.2,yB-sz*.32,sz*.4,sz*.32);
      g.fillStyle='#dc2626';g.fillRect(sx-sz*.05,yB-sz*.26,sz*.1,sz*.2);g.fillRect(sx-sz*.12,yB-sz*.2,sz*.24,sz*.08);
    }else{
      g.fillStyle='#ca8a04';g.fillRect(sx-sz*.2,yB-sz*.26,sz*.4,sz*.26);
      g.fillStyle='#fef08a';g.fillRect(sx-sz*.14,yB-sz*.2,sz*.28,sz*.06);
    }
  }

  let bx=80+Math.cos(gunBob)*3,by=58+Math.abs(Math.sin(gunBob))*3+recoil*1.8;
  if(recoil>5){
    g.fillStyle='#fef08a';g.beginPath();g.arc(bx,by-4,11,0,6.283);g.fill();
    g.fillStyle='#f97316';g.beginPath();g.arc(bx,by-4,6,0,6.283);g.fill();
  }
  g.fillStyle='#3f3f46';g.fillRect(bx-7,by,5,24);g.fillRect(bx+2,by,5,24);
  g.fillStyle='#71717a';g.fillRect(bx-8,by+12,16,14);
  g.fillStyle='rgba(255,255,255,0.6)';g.fillRect(77,41,7,1);g.fillRect(80,38,1,7);

  for(let my=0;my<MH;my++)for(let mx=0;mx<MW;mx++){
    g.fillStyle=MAP[my][mx]!=='0'?'rgba(255,255,255,0.35)':'rgba(0,0,0,0.4)';
    g.fillRect(121+mx*3,3+my*3,3,3);
  }
  g.fillStyle='#4ade80';g.fillRect(120+px*3,2+py*3,2,2);

  if(dmgFlash>0){g.fillStyle='rgba(220,38,38,0.38)';g.fillRect(0,0,W,H)}

  g.fillStyle='#3f3f46';g.fillRect(0,H,W,HUD);
  g.strokeStyle='#71717a';g.strokeRect(0.5,H+0.5,W-1,HUD-1);
  g.fillStyle='#ef4444';g.font='700 8px monospace';
  g.fillText('AMMO',6,H+7);g.fillText('HP',42,H+7);g.fillText('KILLS',116,H+7);
  g.fillStyle='#facc15';g.font='700 9px monospace';
  g.fillText(String(ammo).padStart(2,'0'),8,H+16);g.fillText(`${hp}%`,38,H+16);g.fillText(`${kills}/${TOTAL}`,116,H+16);
  g.fillStyle='#d4a373';g.fillRect(72,H+2,16,14);g.fillStyle='#3f2e21';g.fillRect(72,H+2,16,4);
  let lk=(tick>>5)%3-1;g.fillStyle='#111';g.fillRect(75+lk,H+7,2,2);g.fillRect(82+lk,H+7,2,2);
  g.fillStyle=hp<40?'#dc2626':'#7f1d1d';g.fillRect(76,H+12,8,2);

  if(dead||won){
    g.fillStyle='rgba(0,0,0,0.72)';g.fillRect(16,24,128,34);
    g.fillStyle=won?'#4ade80':'#ef4444';g.font='700 11px monospace';
    g.fillText(won?'STAGE CLEAR!':'YOU DIED',won?42:54,39);
    g.fillStyle='#fff';g.font='700 8px monospace';g.fillText('Tap FIRE to restart',34,51);
  }
  requestAnimationFrame(loop);
}
loop();
</script>
</body>
</html>"""


# ==========================================
# ВЕБ-МАРШРУТЫ ДЛЯ RENDER (подключаются в start_web_server)
# ==========================================
def attach_game_web_routes(app: web.Application, bot: Bot, update_user_balance):
    async def serve_gd(request: web.Request):
        return web.Response(text=GD_HTML, content_type="text/html", charset="utf-8")

    async def serve_doom(request: web.Request):
        return web.Response(text=DOOM_HTML, content_type="text/html", charset="utf-8")

    async def api_game_win(request: web.Request):
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False}, status=400)

        sid = str(data.get("sid") or "")
        uid = int(data.get("uid") or 0)
        sig = str(data.get("sig") or "")
        game = str(data.get("game") or "")

        if not sid or not uid or sig != _sign_session(bot.token, sid, uid):
            return web.json_response({"ok": False}, status=403)

        session = widget_games.get(sid)
        if not session or session.get("rewarded"):
            return web.json_response({"ok": True, "already": True})

        session["rewarded"] = True
        bet = session.get("bet", 0)
        win_amt = (bet * 3) if bet > 0 else 25
        new_bal = await update_user_balance(uid, win_amt)

        game_name = "Geometry Dash" if game == "gd" else "DOOM 2.5D"
        with suppress(Exception):
            await bot.send_message(
                chat_id=session.get("notify_chat_id", uid),
                text=f"🏆 Вы прошли <b>{game_name}</b>! Награда: <b>+{win_amt} монет</b> · Баланс: <b>{new_bal}</b>",
                parse_mode="HTML",
                business_connection_id=session.get("conn_id"),
            )
        return web.json_response({"ok": True, "win": win_amt, "balance": new_bal})

    app.router.add_get("/game/gd", serve_gd)
    app.router.add_get("/game/doom", serve_doom)
    app.router.add_post("/api/game_win", api_game_win)


# ==========================================
# РЕГИСТРАЦИЯ КОМАНД БОТА (.гд / .дум)
# ==========================================
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
    async def _send_webapp_button(
        target_chat_id: int,
        player_id: int,
        game_type: str,
        bet: int,
        notify_chat_id: int,
        notify_conn_id: str | None,
    ):
        base_url = _get_public_url()
        if not base_url:
            await safe_send_msg(
                chat_id=target_chat_id,
                text="⚠️ Не найдена переменная <code>RENDER_EXTERNAL_URL</code> (или <code>PUBLIC_URL</code>) в настройках Render.",
            )
            return

        if bet > 0:
            bal = await get_user_balance(player_id)
            if bal < bet:
                await safe_send_msg(
                    chat_id=target_chat_id,
                    text=f"❌ Недостаточно монет! Нужно: <b>{bet}</b>, у вас: <b>{bal}</b>.",
                )
                return
            await update_user_balance(player_id, -bet)

        sid = f"{player_id}_{game_type}_{os.urandom(4).hex()}"
        sig = _sign_session(bot.token, sid, player_id)
        widget_games[sid] = {
            "owner_id": player_id,
            "bet": bet,
            "rewarded": False,
            "notify_chat_id": notify_chat_id,
            "conn_id": notify_conn_id,
        }

        game_url = f"{base_url}/game/{game_type}?sid={sid}&uid={player_id}&sig={sig}"
        title = "🟦 Geometry Dash" if game_type == "gd" else "🔫 DOOM 2.5D"
        bet_line = f"Ставка: <b>{bet} монет</b> · Приз: <b>{bet * 3} монет</b>" if bet > 0 else "Награда за прохождение: <b>+25 монет</b>"

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"▶️ Играть в {title}", web_app=WebAppInfo(url=game_url))
        ]])
        await safe_send_msg(
            chat_id=target_chat_id,
            text=f"<b>{title}</b>\n{bet_line}\n\nНажми кнопку ниже — игра откроется прямо в Telegram:",
            reply_markup=kb,
        )

    async def _launch_game(message: Message, game_type: str):
        conn_id = getattr(message, "business_connection_id", None)
        bet = parse_optional_bet(message.text)

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

            # Отправляем кнопку Mini App владельцу в ЛС с ботом
            await _send_webapp_button(
                target_chat_id=owner_id,
                player_id=owner_id,
                game_type=game_type,
                bet=bet,
                notify_chat_id=message.chat.id,
                notify_conn_id=conn_id,
            )

            # И в бизнес-чат кидаем ссылку-приглашение, чтобы собеседник тоже мог открыть игру
            bot_uname = await _get_bot_username(bot)
            title = "🟦 Geometry Dash" if game_type == "gd" else "🔫 DOOM 2.5D"
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text=f"🎮 Запустить {title}",
                    url=f"https://t.me/{bot_uname}?start=game_{game_type}",
                )
            ]]) if bot_uname else None
            await safe_send_msg(
                chat_id=message.chat.id,
                text=f"<b>{title}</b> — нажми кнопку ниже, чтобы играть прямо в Telegram!",
                reply_markup=kb,
                business_connection_id=conn_id,
            )
            return

        # Личка с ботом: кнопка WebApp открывает игру сразу здесь
        if message.chat.type == "private":
            await _send_webapp_button(
                target_chat_id=message.chat.id,
                player_id=message.from_user.id,
                game_type=game_type,
                bet=bet,
                notify_chat_id=message.chat.id,
                notify_conn_id=None,
            )
        else:
            # Группа (в группах кнопки web_app запрещены Telegram — ведём в ЛС бота)
            bot_uname = await _get_bot_username(bot)
            title = "🟦 Geometry Dash" if game_type == "gd" else "🔫 DOOM 2.5D"
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text=f"🎮 Открыть {title}",
                    url=f"https://t.me/{bot_uname}?start=game_{game_type}",
                )
            ]]) if bot_uname else None
            await safe_send_msg(
                chat_id=message.chat.id,
                text=f"<b>{title}</b> — нажми кнопку, чтобы запустить игру в боте!",
                reply_markup=kb,
            )

    @dp.business_message(F.text.lower().regexp(r"^\.(гд|геометридеш|gd)(?:\s+\d+)?$"))
    @dp.message(F.text.lower().regexp(r"^\.(гд|геометридеш|gd)(?:\s+\d+)?$"))
    async def cmd_gd_widget(message: Message):
        await _launch_game(message, "gd")

    @dp.business_message(F.text.lower().regexp(r"^\.(дум|doom)(?:\s+\d+)?$"))
    @dp.message(F.text.lower().regexp(r"^\.(дум|doom)(?:\s+\d+)?$"))
    async def cmd_doom_widget(message: Message):
        await _launch_game(message, "doom")

    @dp.message(F.text.regexp(r"^/start\s+game_(gd|doom)$"))
    async def cmd_deeplink_game(message: Message):
        game_type = "gd" if message.text.endswith("gd") else "doom"
        await _send_webapp_button(
            target_chat_id=message.chat.id,
            player_id=message.from_user.id,
            game_type=game_type,
            bet=0,
            notify_chat_id=message.chat.id,
            notify_conn_id=None,
        )
