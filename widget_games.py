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


# ==========================================
# GEOMETRY DASH — ДЕТАЛИЗИРОВАННЫЙ ВИДЖЕТ (~1.8 КБ zlib)
# ==========================================
GD_WIDGET_HTML = (
    "<!doctype html><meta charset=utf-8><style>"
    "*{margin:0;padding:0;user-select:none;touch-action:none}"
    "body{background:#090d16;overflow:hidden;font:700 12px sans-serif;color:#fff}"
    "canvas{display:block;width:100%;height:220px}</style>"
    "<canvas id=c width=340 height=220></canvas><script>"
    "const D=document,C=D.getElementById('c'),g=C.getContext('2d'),W=340,H=220,FY=178,S=22,PX=64,GL=3150,M=Math,"
    "R=(x,y,w,h,f)=>{g.fillStyle=f;g.fillRect(x,y,w,h)},"
    "L=[[280,0,0],[440,0,0],[462,0,0],[620,1,0],[644,1,0],[668,1,0],[668,0,24],[820,2,0],"
    "[900,1,24],[924,1,24],[948,1,48],[972,1,48],[996,1,48],[1140,0,0],[1162,0,0],[1184,0,0],"
    "[1280,1,0],[1350,3,52],[1430,1,48],[1454,1,48],[1478,1,48],[1560,0,0],[1582,0,0],[1660,2,0],"
    "[1750,1,48],[1774,1,48],[1798,0,72],[1920,1,0],[1944,1,24],[1968,1,48],[2040,3,74],"
    "[2120,1,48],[2144,1,48],[2168,1,24],[2192,1,0],[2320,0,0],[2342,0,0],[2440,2,0],[2510,3,76],"
    "[2590,1,72],[2614,1,72],[2638,1,72],[2740,0,0],[2762,0,0],[2840,1,0],[2864,1,0],[2920,0,0],[2942,0,0]];"
    "let y=FY-S,vy=0,rt=0,cm=0,at=1,hd=0,tp=0,og=1,dd=0,wn=0,sh=0,jp=0,ps=[],tr=[],uo=new Set();"
    "C.onpointerdown=()=>{hd=1;tp=1;if(wn){wn=0;rs(1)}};"
    "C.onpointerup=C.onpointercancel=()=>hd=0;"
    "const rs=a=>{cm=0;y=FY-S;vy=0;rt=0;og=1;dd=0;tr=[];uo.clear();if(a)at++},"
    "bu=(x,y,f,n)=>{for(let i=0;i<n;i++){let a=M.random()*6.28,s=1.5+M.random()*4.5;"
    "ps.push({x,y,vx:M.cos(a)*s,vy:M.sin(a)*s-1.5,l:24+M.random()*14,s:3+M.random()*4,c:f})}},"
    "di=()=>{if(dd)return;dd=30;sh=8;bu(PX+11,y+11,'#facc15',16);bu(PX+11,y+11,'#38bdf8',10)};"
    "function lp(){"
    "let pc=M.min(100,(cm/GL*100)|0),hu=(215+pc*1.4)%360,bg=g.createLinearGradient(0,0,0,H);"
    "bg.addColorStop(0,`hsl(${hu},55%,12%)`);bg.addColorStop(1,`hsl(${(hu+30)%360},65%,20%)`);"
    "R(0,0,W,H,bg);g.save();"
    "if(sh>0){g.translate((M.random()-.5)*sh,(M.random()-.5)*sh);sh*=.85}"
    "g.strokeStyle='rgba(255,255,255,.06)';g.lineWidth=1;"
    "for(let i=-(cm*.3)%40;i<W;i+=40){g.beginPath();g.moveTo(i,0);g.lineTo(i,FY);g.stroke()}"
    "for(let j=18;j<FY;j+=40){g.beginPath();g.moveTo(0,j);g.lineTo(W,j);g.stroke()}"
    "g.fillStyle='rgba(255,255,255,.05)';"
    "for(let i=0;i<6;i++)g.fillRect(((i*110-cm*.18)%660+660)%660-80,45+(i%3)*22,42,FY-45);"
    "R(0,FY,W,H-FY,`hsl(${hu},60%,10%)`);"
    "g.strokeStyle=`hsl(${hu},90%,60%)`;g.lineWidth=2;g.beginPath();g.moveTo(0,FY);g.lineTo(W,FY);g.stroke();"
    "g.strokeStyle='rgba(255,255,255,.12)';g.lineWidth=1;"
    "for(let i=-(cm%28);i<W;i+=28)g.strokeRect(i,FY+4,24,24);"
    "if(!dd&&!wn){"
    "let py=y;cm+=4.1;"
    "if(hd&&og){vy=-8.8;og=0;jp++;bu(PX+4,y+S,'#38bdf8',4)}"
    "vy=M.min(12,vy+.52);y+=vy;og=0;"
    "if(y>=FY-S){y=FY-S;vy=0;og=1}"
    "for(let i=0;i<L.length;i++){"
    "let[ox,t,oh]=L[i],sx=ox-cm+PX,by=FY-24-oh;"
    "if(sx<-40||sx>W+40)continue;"
    "if(t===1&&sx<PX+S-3&&sx+24>PX+3){if(py+S<=by+10&&vy>=0&&y+S>=by){y=by-S;vy=0;og=1}else if(y+S>by+4&&y<by+20)di()}"
    "else if(t===0&&sx+5<PX+S-3&&sx+17>PX+3&&y+S>FY-oh-16&&y<FY-oh)di();"
    "else if(t===2&&sx<PX+S&&sx+24>PX&&y+S>=FY-oh-8&&y+S<=FY-oh+6){vy=-11.2;og=0;bu(sx+12,FY-oh-4,'#facc15',8)}"
    "else if(t===3&&tp&&!uo.has(i)&&M.hypot(sx+12-(PX+11),FY-oh-12-(y+11))<28){vy=-9.2;og=0;uo.add(i);bu(sx+12,FY-oh-12,'#fde047',10)}"
    "}"
    "rt=og?M.round(rt/90)*90:rt+6.5;"
    "tr.push({x:PX+6,y:y+11,a:.7});if(tr.length>14)tr.shift();"
    "if(cm>=GL){wn=1;bu(PX+11,y+11,'#4ade80',25);try{TelegramWidget.request('gd_win')}catch(_){}}"
    "}else if(dd>0&&--dd===0)rs(1);"
    "tp=0;"
    "for(let[ox,t,oh]of L){"
    "let sx=ox-cm+PX,by=FY-24-oh,sy=FY-oh;"
    "if(sx<-32||sx>W+32)continue;"
    "if(t===1){R(sx,by,24,24,'#1e1b4b');g.strokeStyle='#818cf8';g.lineWidth=2;g.strokeRect(sx+1,by+1,22,22);R(sx+5,by+5,14,14,'rgba(129,140,248,.22)')}"
    "else if(t===0){g.fillStyle='#0f172a';g.strokeStyle='#f43f5e';g.lineWidth=2;g.beginPath();g.moveTo(sx+1,sy);g.lineTo(sx+11,sy-21);g.lineTo(sx+21,sy);g.closePath();g.fill();g.stroke()}"
    "else if(t===2){g.fillStyle='#facc15';g.beginPath();g.arc(sx+12,sy,11,M.PI,0);g.fill();R(sx+4,sy-3,16,3,'#fef08a')}"
    "else{g.strokeStyle='#fde047';g.lineWidth=2.5;g.beginPath();g.arc(sx+12,sy-12,11,0,6.28);g.stroke();g.fillStyle='rgba(250,204,21,.45)';g.beginPath();g.arc(sx+12,sy-12,7,0,6.28);g.fill()}"
    "}"
    "let fp=GL-cm+PX;if(fp>-20&&fp<W+40){g.strokeStyle='#4ade80';g.lineWidth=4;g.beginPath();g.ellipse(fp+12,FY-48,10,44,0,0,6.28);g.stroke()}"
    "for(let p of tr){p.x-=4.1;p.a*=.88;R(p.x-3,p.y-3,6,6,`rgba(56,189,248,${p.a})`)}"
    "if(!dd){g.save();g.translate(PX+11,y+11);g.rotate(rt*M.PI/180);R(-11,-11,S,S,'#facc15');g.strokeStyle='#090d16';g.lineWidth=2;g.strokeRect(-11,-11,S,S);R(-6,-6,12,12,'#38bdf8');R(-4,-4,3,3,'#090d16');R(1,-4,3,3,'#090d16');R(-4,2,8,2,'#090d16');g.restore()}"
    "for(let i=ps.length-1;i>=0;i--){let p=ps[i];p.x+=p.vx;p.y+=p.vy;p.vy+=.18;p.l--;g.globalAlpha=M.max(0,p.l/30);R(p.x,p.y,p.s,p.s,p.c);if(p.l<=0)ps.splice(i,1)}"
    "g.globalAlpha=1;g.restore();"
    "R(80,10,180,8,'rgba(0,0,0,.45)');R(80,10,1.8*pc,8,'#4ade80');g.strokeStyle='rgba(255,255,255,.4)';g.strokeRect(80,10,180,8);"
    "g.fillStyle='#fff';g.fillText(`ATT ${at}`,10,18);g.fillText(`${pc}%`,268,18);"
    "if(wn){R(35,68,270,66,'rgba(0,0,0,.65)');g.strokeStyle='#4ade80';g.lineWidth=2;g.strokeRect(35,68,270,66);"
    "g.fillStyle='#4ade80';g.font='700 16px sans-serif';g.fillText('LEVEL COMPLETE!',95,94);"
    "g.fillStyle='#fff';g.font='700 12px sans-serif';g.fillText(`Att: ${at} • Jumps: ${jp} • Tap to replay`,58,118)}"
    "requestAnimationFrame(lp)}lp()</script>"
)


# ==========================================
# DOOM — ДЕТАЛИЗИРОВАННЫЙ 2.5D RAYCASTER (~2.3 КБ zlib)
# ==========================================
DOOM_WIDGET_HTML = (
    "<!doctype html><meta charset=utf-8><style>"
    "*{margin:0;padding:0;box-sizing:border-box;user-select:none;touch-action:none}"
    "body{background:#09090b;color:#fff;font:700 11px monospace;overflow:hidden}"
    "#v{display:block;width:100%;height:184px;image-rendering:pixelated}"
    ".p{display:grid;grid-template-columns:1fr 1fr 1fr 1fr 1.4fr;gap:4px;padding:4px;height:56px;background:#18181b;border-top:2px solid #3f3f46}"
    "button{background:#27272a;color:#e4e4e7;border:1px solid #52525b;border-radius:6px;font:700 16px sans-serif}"
    "button:active{background:#52525b}"
    "#f{background:#991b1b;border-color:#ef4444;color:#fff;font-size:14px}#f:active{background:#dc2626}</style>"
    "<canvas id=v width=160 height=100></canvas>"
    "<div class=p><button id=l>◀</button><button id=u>▲</button><button id=d>▼</button><button id=r>▶</button><button id=f>🔥 FIRE</button></div><script>"
    "const D=document,Q=i=>D.getElementById(i),C=Q('v'),g=C.getContext('2d'),M=Math,W=160,H=82,U=18,"
    "I=g.createImageData(W,H),B=I.data,"
    "R=(x,y,w,h,c)=>{g.fillStyle=c;g.fillRect(x,y,w,h)},"
    "K=(x,y,r,c)=>{g.fillStyle=c;g.beginPath();g.arc(x,y,r,0,6.28);g.fill()},"
    "MP=['111122221111','100000000001','102200003301','102000000301','200004400002','200004400002','103000000201','103300002201','100000000001','111122221111'],"
    "TX=[];"
    "for(let t=1;t<5;t++){let a=new Uint8Array(768);for(let y=0;y<16;y++)for(let x=0;x<16;x++){"
    "let i=(y*16+x)*3,r=90,gr=90,b=90;"
    "if(t===1){let m=y%4===0||(x+(y>>2)*4)%8===0;r=gr=b=m?42:118+((x*37+y*17)&15)-8}"
    "else if(t===2){let e=!x||x===15||!y||y===15,p=x>3&&x<12&&y>3&&y<12;r=e?45:p?60:85;gr=e?50:p?110:92;b=e?65:p?175:110}"
    "else if(t===3){let m=y%4===0||(x+(y>>2)*3)%6===0;r=m?55:155+((x*13+y*7)&15);gr=m?18:38;b=m?18:34}"
    "else{let s=((x+y)&3)===0;r=s?40:55;gr=s?155:115;b=45}"
    "a[i]=r;a[i+1]=gr;a[i+2]=b}TX[t]=a}"
    "let px,py,pa,hp,am,kl,gb=0,rc,df,wn,dd,tk=0,ks={},E=[];"
    "const ini=()=>{px=1.5;py=1.5;pa=.2;hp=100;am=24;kl=rc=df=wn=dd=0;"
    "E=[[5.5,1.5,0,3,50],[9.5,1.8,1,4,70],[10.2,5,0,3,60],[6.5,8.2,1,4,65],[2.5,8.2,0,3,55],[1.5,5,2,1,0],[8.5,5,3,1,0]]"
    ".map(([x,y,t,h,c])=>({x,y,t,h,c,f:0}))};ini();"
    "['l','u','d','r'].forEach(i=>{let b=Q(i);b.onpointerdown=()=>ks[i]=1;b.onpointerup=b.onpointercancel=()=>ks[i]=0});"
    "const wa=(x,y)=>{let mx=x|0,my=y|0;return my<0||my>9||mx<0||mx>11?1:+MP[my][mx]},"
    "los=(a,b,c,d)=>{let x=c-a,y=d-b,n=M.ceil(M.hypot(x,y)*5);for(let i=1;i<n;i++)if(wa(a+x*i/n,b+y*i/n))return 0;return 1};"
    "Q('f').onpointerdown=()=>{"
    "if(dd||wn){ini();return}"
    "if(rc||am<=0)return;am--;rc=9;"
    "let bs=0,bd=99;"
    "for(let e of E){if(e.h<=0||e.t>1)continue;let dx=e.x-px,dy=e.y-py,d=M.hypot(dx,dy),a=M.atan2(dy,dx)-pa;"
    "while(a>M.PI)a-=6.283;while(a<-M.PI)a+=6.283;"
    "if(M.abs(a)<.22&&d<8&&d<bd&&los(px,py,e.x,e.y)){bs=e;bd=d}}"
    "if(bs){bs.h--;bs.f=6;if(bs.h<=0&&++kl>=5){wn=1;try{TelegramWidget.request('doom_win')}catch(_){}}}"
    "};"
    "function lp(){"
    "tk++;"
    "if(!dd&&!wn){"
    "if(ks.l)pa-=.065;if(ks.r)pa+=.065;"
    "let mv=(ks.u?1:0)-(ks.d?1:0);"
    "if(mv){let nx=px+M.cos(pa)*.075*mv,ny=py+M.sin(pa)*.075*mv;"
    "if(!wa(nx+M.sign(nx-px)*.22,py))px=nx;if(!wa(px,ny+M.sign(ny-py)*.22))py=ny;gb+=.28}"
    "for(let e of E){if(e.h<=0)continue;let dx=px-e.x,dy=py-e.y,d=M.hypot(dx,dy);"
    "if(e.t===2&&d<.65&&hp<100){hp=M.min(100,hp+35);e.h=0;continue}"
    "if(e.t===3&&d<.65){am+=12;e.h=0;continue}"
    "if(e.t<2){if(e.f)e.f--;if(d<6.5&&los(e.x,e.y,px,py)){"
    "if(d>1.2){let ex=e.x+dx/d*.018,ey=e.y+dy/d*.018;if(!wa(ex,e.y))e.x=ex;if(!wa(e.x,ey))e.y=ey}"
    "if(--e.c<=0){e.c=e.t?58:72;hp=M.max(0,hp-(e.t?11:8));df=7;if(!hp)dd=1}}}}}"
    "if(rc)rc--;if(df)df--;"
    "for(let y=0;y<H;y++){let f=y>=41,d=f?(y-41)/41:(41-y)/41,"
    "r=f?24+d*46:18+d*22,gr=f?22+d*38:16+d*16,b=f?24+d*34:22+d*20;"
    "for(let x=0;x<W;x++){let i=(y*W+x)*4;B[i]=r;B[i+1]=gr;B[i+2]=b;B[i+3]=255}}"
    "let dx=M.cos(pa),dy=M.sin(pa),plx=-dy*.66,ply=dx*.66,zb=new Float32Array(W);"
    "for(let x=0;x<W;x++){"
    "let cx=2*x/W-1,rx=dx+plx*cx,ry=dy+ply*cx,mx=px|0,my=py|0,"
    "ddx=M.abs(1/(rx||1e-6)),ddy=M.abs(1/(ry||1e-6)),sx=rx<0?-1:1,sy=ry<0?-1:1,"
    "sdx=rx<0?(px-mx)*ddx:(mx+1-px)*ddx,sdy=ry<0?(py-my)*ddy:(my+1-py)*ddy,ht=0,sd=0;"
    "for(let k=0;k<22&&!ht;k++){if(sdx<sdy){sdx+=ddx;mx+=sx;sd=0}else{sdy+=ddy;my+=sy;sd=1}ht=wa(mx,my)}"
    "let ds=M.max(.15,sd?sdy-ddy:sdx-ddx);zb[x]=ds;"
    "let lh=(H/ds)|0,y0=M.max(0,(H-lh)>>1),y1=M.min(H-1,(H+lh)>>1),"
    "wx=sd?px+ds*rx:py+ds*ry;wx-=wx|0;let tx=((wx*16)|0)&15,te=TX[ht]||TX[1],sh=(sd?.68:1)*M.max(.22,1-ds/11);"
    "for(let y=y0;y<=y1;y++){let ty=((((y-(H-lh)/2)/lh)*16)|0)&15,ti=(ty*16+tx)*3,bi=(y*W+x)*4;"
    "B[bi]=te[ti]*sh;B[bi+1]=te[ti+1]*sh;B[bi+2]=te[ti+2]*sh}}"
    "g.putImageData(I,0,0);"
    "for(let e of E.filter(e=>e.h>0).sort((a,b)=>M.hypot(b.x-px,b.y-py)-M.hypot(a.x-px,a.y-py))){"
    "let ex=e.x-px,ey=e.y-py,iv=1/(plx*dy-dx*ply),tx=iv*(dy*ex-dx*ey),ty=iv*(-ply*ex+plx*ey);"
    "if(ty<=.25)continue;let sx=(80*(1+tx/ty))|0,sz=M.min(76,(H/ty)|0),cl=M.max(0,M.min(W-1,sx));"
    "if(zb[cl]<ty)continue;let yb=41+sz/2;"
    "if(!e.t){R(sx-sz*.26,yb-sz*.88,sz*.52,sz*.82,e.f?'#fff':'#92400e');R(sx-sz*.14,yb-sz*.74,sz*.1,sz*.1,'#ef4444');R(sx+sz*.04,yb-sz*.74,sz*.1,sz*.1,'#ef4444');R(sx-sz*.1,yb-sz*.56,sz*.2,sz*.08,'#fbbf24')}"
    "else if(e.t===1){K(sx,41-sz*.08,sz*.34,e.f?'#fff':'#dc2626');K(sx,41-sz*.14,sz*.12,'#22c55e');R(sx-sz*.18,41+sz*.04,sz*.36,sz*.1,'#1e1b4b')}"
    "else if(e.t===2){R(sx-sz*.2,yb-sz*.32,sz*.4,sz*.32,'#f8fafc');R(sx-sz*.05,yb-sz*.26,sz*.1,sz*.2,'#dc2626');R(sx-sz*.12,yb-sz*.2,sz*.24,sz*.08,'#dc2626')}"
    "else{R(sx-sz*.2,yb-sz*.26,sz*.4,sz*.26,'#ca8a04');R(sx-sz*.14,yb-sz*.2,sz*.28,sz*.06,'#fef08a')}}"
    "let bx=80+M.cos(gb)*3,by=58+M.abs(M.sin(gb))*3+rc*1.8;"
    "if(rc>5){K(bx,by-4,11,'#fef08a');K(bx,by-4,6,'#f97316')}"
    "R(bx-7,by,5,24,'#3f3f46');R(bx+2,by,5,24,'#3f3f46');R(bx-8,by+12,16,14,'#71717a');"
    "R(77,41,7,1,'rgba(255,255,255,.6)');R(80,38,1,7,'rgba(255,255,255,.6)');"
    "for(let y=0;y<10;y++)for(let x=0;x<12;x++)R(121+x*3,3+y*3,3,3,MP[y][x]!=='0'?'rgba(255,255,255,.35)':'rgba(0,0,0,.4)');"
    "R(120+px*3,2+py*3,2,2,'#4ade80');"
    "if(df)R(0,0,W,H,'rgba(220,38,38,.38)');"
    "R(0,H,W,U,'#3f3f46');g.strokeStyle='#71717a';g.strokeRect(.5,H+.5,W-1,U-1);"
    "g.fillStyle='#ef4444';g.font='700 8px monospace';g.fillText('AMMO',6,H+7);g.fillText('HP',42,H+7);g.fillText('KILLS',116,H+7);"
    "g.fillStyle='#facc15';g.font='700 9px monospace';g.fillText(String(am).padStart(2,'0'),8,H+16);g.fillText(`${hp}%`,38,H+16);g.fillText(`${kl}/5`,116,H+16);"
    "R(72,H+2,16,14,'#d4a373');R(72,H+2,16,4,'#3f2e21');"
    "let lk=(tk>>5)%3-1;R(75+lk,H+7,2,2,'#111');R(82+lk,H+7,2,2,'#111');R(76,H+12,8,2,hp<40?'#dc2626':'#7f1d1d');"
    "if(dd||wn){R(16,24,128,34,'rgba(0,0,0,.68)');g.fillStyle=wn?'#4ade80':'#ef4444';g.font='700 11px monospace';"
    "g.fillText(wn?'STAGE CLEAR!':'YOU DIED',wn?42:54,39);g.fillStyle='#fff';g.font='700 8px monospace';g.fillText('Tap FIRE to restart',34,51)}"
    "requestAnimationFrame(lp)}lp()</script>"
)


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
