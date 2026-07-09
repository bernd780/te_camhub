"use strict";
const $=s=>document.querySelector(s);
const el=(t,c,h)=>{const e=document.createElement(t);if(c)e.className=c;if(h!=null)e.innerHTML=h;return e;};
const api=async(path,opts)=>{const r=await fetch(path,Object.assign({credentials:"same-origin"},opts||{}));
  if(r.status===401){showAuth();throw new Error("auth");}return r;};
const jget=async p=>(await api(p)).json();
const jpost=async(p,b)=>(await api(p,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b||{})})).json();
function toast(t){const el=$("#toast");el.textContent=t;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),2200);}

/* ---------------- auth ---------------- */
let SETUP=false;
async function boot(){
  const st=await (await fetch("api/vault/status",{credentials:"same-origin"})).json();
  if(st.session){startApp();return;}
  showAuth(st);
}
function showAuth(st){
  $("#app").classList.add("hidden");$("#auth").classList.remove("hidden");
  st=st||{};
  SETUP=!st.has_vault;
  $("#auth_title").textContent=SETUP?"Tresor einrichten":"TeslaCam Hub";
  $("#auth_sub").textContent=SETUP?"Lege ein Passwort fest. Ohne dieses Passwort sind die Aufnahmen nicht entschlüsselbar.":"Bitte anmelden.";
  $("#auth_pass2").classList.toggle("hidden",!SETUP);
  $("#auth_import_l").classList.toggle("hidden",!SETUP);
  $("#auth_go").textContent=SETUP?"Einrichten":"Anmelden";
  $("#auth_pass").value="";$("#auth_pass2").value="";$("#auth_msg").textContent="";
  $("#auth_pass").focus();
}
async function doAuth(){
  const m=$("#auth_msg");m.className="msg";
  const p=$("#auth_pass").value;if(!p){m.textContent="Passwort erforderlich";return;}
  try{
    if(SETUP){
      if(p!==$("#auth_pass2").value){m.className="msg err";m.textContent="Passwörter stimmen nicht überein";return;}
      m.textContent="Richte ein…";
      const r=await jpost("api/setup",{pass:p,import:$("#auth_import").checked});
      if(r.ok){startApp();toast("Tresor eingerichtet ("+(r.imported||0)+" Schlüssel)");}
      else{m.className="msg err";m.textContent=r.error||"Fehler";}
    }else{
      m.textContent="Anmelden…";
      const r=await jpost("api/login",{pass:p});
      if(r.ok)startApp();else{m.className="msg err";m.textContent=r.error||"falsches Passwort";}
    }
  }catch(e){m.className="msg err";m.textContent="Verbindungsfehler";}
}

/* ---------------- shell ---------------- */
function startApp(){
  $("#auth").classList.add("hidden");$("#app").classList.remove("hidden");
  const foot=document.querySelector(".foot a");if(foot)foot.href="http://"+location.hostname+":8080/";
  render("clips");
}
document.querySelectorAll("nav .nav[data-view]").forEach(a=>a.onclick=()=>{
  document.querySelectorAll("nav .nav").forEach(n=>n.classList.remove("active"));
  a.classList.add("active");render(a.dataset.view);
});
$("#lockbtn").onclick=async()=>{try{await jpost("api/logout",{});}catch(e){}showAuth();};
$("#auth_go").onclick=doAuth;
["auth_pass","auth_pass2"].forEach(id=>$("#"+id).addEventListener("keydown",e=>{if(e.key==="Enter")doAuth();}));

function render(view){
  const m=$("#main");m.innerHTML="";
  if(view==="clips")return viewClips(m);
  if(view==="files")return viewFiles(m,"");
  if(view==="diag")return viewDiag(m);
  if(view==="settings")return viewSettings(m);
}

/* ---------------- Aufnahmen ---------------- */
async function viewClips(m){
  m.innerHTML="";
  m.append(el("h2","title","Aufnahmen"));
  const info=el("div","sub","lädt…");m.append(info);
  const nasrow=el("div","sub nasrow","NAS-Archiv: lädt…");m.append(nasrow);
  refreshNasStatus(nasrow);
  const bar=el("div","saverow");
  const bulkbtn=el("button","btn sm","🔓 Alle entschlüsseln + Metadaten erzeugen");
  const bulkmsg=el("span","note","");
  const syncbtn=el("button","btn sm ghost","🔄 Jetzt synchronisieren");
  const syncmsg=el("span","note","");
  bar.append(bulkbtn,bulkmsg,syncbtn,syncmsg);m.append(bar);
  const grid=el("div","clipgrid");m.append(grid);
  let clips;try{clips=await jget("api/clips");}catch(e){return;}
  const enc=clips.filter(c=>c.encrypted).length;
  info.textContent=`${clips.length} Clips · ${enc} verschlüsselt`;
  let nasClips={};try{nasClips=(await jget("api/nas/sync_status")).clips||{};}catch(e){}
  if(!clips.length){info.textContent="Keine Aufnahmen gefunden.";return;}
  syncbtn.onclick=async()=>{
    syncbtn.disabled=true;syncmsg.textContent="starte Archivierung…";
    try{await jpost("api/sync",{});}catch(e){}
    syncmsg.textContent="Archivierung läuft (Auto trennt kurz die USB-Verbindung)…";
    setTimeout(async()=>{
      await jpost("api/nas/sync_status/refresh",{});
      setTimeout(()=>{syncbtn.disabled=false;syncmsg.textContent="✓ ausgelöst";toast("Sync ausgelöst, Status aktualisiert sich gleich");viewClips(m);},15000);
    },20000);
  };
  bulkbtn.onclick=async()=>{
    bulkbtn.disabled=true;bulkmsg.textContent="startet…";
    let st;try{st=await jpost("api/bulk_prepare",{});}catch(e){bulkbtn.disabled=false;bulkmsg.textContent="✗ Fehler";return;}
    const poll=async()=>{
      try{st=await jget("api/bulk_prepare");}catch(e){return;}
      bulkmsg.textContent=st.total?`${st.done} / ${st.total}…`:"nichts zu tun";
      if(st.running){setTimeout(poll,1500);}
      else{
        bulkbtn.disabled=false;
        bulkmsg.textContent=st.errors&&st.errors.length?`fertig, ${st.errors.length} Fehler`:(st.total?"✓ fertig":"nichts zu tun");
        toast("Entschlüsselung abgeschlossen");
        viewClips(m);
      }
    };
    setTimeout(poll,1200);
  };
  clips.forEach(c=>{
    const card=el("div","clip");
    const th=el("div","thumb");th.append(el("div","ph","🎞️"));card.append(th);
    const src=c.encrypted&&!c.playable?null:"api/thumb?id="+encodeURIComponent(c.id);
    if(src){const img=new Image();img.onload=()=>{th.style.backgroundImage=`url(${src})`;th.querySelector(".ph").remove();};img.src=src;}
    const meta=el("div","meta");
    meta.append(el("div","t",c.timestamp.replace("_"," ").replace(/-/g,(x,i)=>i<7?"-":":")));
    const b=el("div","badges");
    b.append(el("span","badge "+(c.encrypted?"enc":"plain"),c.encrypted?"🔒 verschlüsselt":"offen"));
    if(c.has_event)b.append(el("span","badge event",c.reason||"Event"));
    if(c.has_locked)b.append(el("span","badge locked","kein Schlüssel"));
    b.append(el("span","badge "+(nasClips[c.id]?"nasok":"nasno"),nasClips[c.id]?"☁️ auf NAS":"☁️ noch nicht"));
    meta.append(b);card.append(meta);
    card.onclick=()=>openClip(c);
    grid.append(card);
  });
}
async function refreshNasStatus(nasrow){
  let s;try{s=await jget("api/nas/sync_status");}catch(e){return;}
  nasrow.innerHTML="";
  if(s.ok===null){nasrow.append(el("span",null,"NAS-Archiv: noch nicht geprüft "));}
  else if(!s.ok){nasrow.append(el("span",null,"NAS-Archiv: nicht erreichbar ("+(s.error||"Fehler")+") "));}
  else{nasrow.append(el("span",null,`NAS-Archiv: ${s.percent}% archiviert (${s.on_nas}/${s.total} Clips) `));}
  const rl=el("a",null,"jetzt prüfen");
  rl.onclick=async()=>{nasrow.querySelector("span").textContent="NAS-Archiv: prüfe…";await jpost("api/nas/sync_status/refresh",{});setTimeout(()=>refreshNasStatus(nasrow),20000);};
  nasrow.append(rl);
}
/* ---------------- Player: synced multi-cam, event-seek, GPS map, HUD ---------------- */
const CAMS=[["front","Front","a-front"],["back","Heck","a-back"],
  ["left_repeater","Links","a-left"],["right_repeater","Rechts","a-right"],
  ["left_pillar","Links (Säule)","a-lp"],["right_pillar","Rechts (Säule)","a-rp"]];
const REASON_LABELS={
  user_interaction_dashcam_icon_tapped:"Dashcam-Taste",
  user_interaction_dashcam_panel_save:"manuell gespeichert",
  sentry_aware_object_detection:"Sentry: Objekt erkannt",
  sentry_aware_accel_detection:"Sentry: Erschütterung",
  sentry_aware_alarm_state:"Sentry: Alarm",
  honk:"Hupe"};
let PLAYER={videos:[],master:null,raf:0,tele:null,gpsPts:[],lmap:null,lmark:null,ltrack:null,event:null,initialSeek:null,cid:null};

function pSlaves(fn){PLAYER.videos.forEach(v=>{if(v!==PLAYER.master)fn(v);});}
function pFmt(t){t=Math.max(0,t||0);const m=Math.floor(t/60),s=Math.floor(t%60);return m+":"+String(s).padStart(2,"0");}

function pClearStage(){
  cancelAnimationFrame(PLAYER.raf);
  PLAYER.videos=[];PLAYER.master=null;PLAYER.tele=null;PLAYER.gpsPts=[];
  PLAYER.lmark=null;PLAYER.ltrack=null;PLAYER.event=null;PLAYER.initialSeek=null;
  if(PLAYER.lmap){PLAYER.lmap.remove();PLAYER.lmap=null;}
}

function pEnsureMap(){
  if(PLAYER.lmap||!window.L)return;
  PLAYER.lmap=L.map($("#pmap"),{attributionControl:false}).setView([0,0],2);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:19}).addTo(PLAYER.lmap);
}
function pDrawTrack(){
  if(!PLAYER.lmap)return;
  if(PLAYER.ltrack){PLAYER.lmap.removeLayer(PLAYER.ltrack);PLAYER.ltrack=null;}
  if(PLAYER.lmark){PLAYER.lmap.removeLayer(PLAYER.lmark);PLAYER.lmark=null;}
  if(!PLAYER.gpsPts.length)return;
  PLAYER.ltrack=L.polyline(PLAYER.gpsPts,{color:"#e63946",weight:3}).addTo(PLAYER.lmap);
  PLAYER.lmark=L.circleMarker(PLAYER.gpsPts[0],{radius:6,color:"#4aa3ff",fillColor:"#4aa3ff",fillOpacity:1}).addTo(PLAYER.lmap);
  PLAYER.lmap.fitBounds(PLAYER.ltrack.getBounds(),{padding:[20,20]});
}
function pShowMap(on){
  const box=$("#pmapbox");if(!box)return;
  box.classList.toggle("hidden",!on);
  if(on){pEnsureMap();pDrawTrack();setTimeout(()=>PLAYER.lmap&&PLAYER.lmap.invalidateSize(),150);}
}
function pMapMarker(f){if(PLAYER.lmark&&f&&f.lat&&f.lon)PLAYER.lmark.setLatLng([f.lat,f.lon]);}

function pHud(f){
  if(!f)return;
  $("#h-gear").textContent=f.gear||"–";
  $("#h-spd").textContent=Math.round(Math.abs(f.speed_kmh||0));
  $("#h-l").classList.toggle("on",!!f.blink_l);
  $("#h-r").classList.toggle("on",!!f.blink_r);
  $("#h-accel-fill").style.height=Math.max(0,Math.min(100,(f.accel||0)*10))+"%";
  $("#h-brake").classList.toggle("on",!!f.brake);
  $("#h-ap").style.display=(f.autopilot>0)?"flex":"none";
  const steer=f.steer||0;
  $("#h-wheel").style.transform="rotate("+steer+"deg)";
  $("#h-wheel").classList.toggle("on",Math.abs(steer)>3);
}
function pNerdLines(f){
  const l=[];
  if(f)l.push(`t=${f.t}s v=${(f.speed_kmh||0).toFixed(1)}km/h gear=${f.gear} steer=${(f.steer||0).toFixed(1)}° accel=${(f.accel||0).toFixed(1)} brake=${f.brake} blink=${f.blink_l?"L":""}${f.blink_r?"R":""} ap=${f.autopilot} gps=${f.lat||"–"},${f.lon||"–"} heading=${f.heading||"–"}`);
  if(PLAYER.event){
    const e=PLAYER.event;
    l.push(`Event: ${REASON_LABELS[e.reason]||e.reason||"–"}${e.city?" @ "+e.city+(e.street?" / "+e.street:""):""}${(e.seek!=null)?` (t=${e.seek.toFixed(1)}s)`:""}`);
  }
  return l.join("\n");
}

function pLoop(){
  const m=PLAYER.master;if(!m)return;
  const t=m.currentTime;
  pSlaves(v=>{if(Math.abs(v.currentTime-t)>0.12)v.currentTime=t;});
  const seekEl=$("#pseek"),timeEl=$("#ptime");
  if(seekEl&&!seekEl.matches(":active"))seekEl.value=Math.floor(t*1000);
  if(timeEl)timeEl.textContent=pFmt(t)+" / "+pFmt(m.duration||0);
  if(PLAYER.tele&&PLAYER.tele.frame_count){
    const i=Math.min(PLAYER.tele.frame_count-1,Math.max(0,Math.round(t*PLAYER.tele.fps)));
    const fr=PLAYER.tele.frames[i];
    if($("#t_hud")&&$("#t_hud").checked)pHud(fr);
    if($("#t_nerd")&&$("#t_nerd").checked){$("#pnerd").textContent=pNerdLines(fr);$("#pnerd").style.display="block";}
    else if($("#pnerd"))$("#pnerd").style.display="none";
    if($("#t_map")&&$("#t_map").checked)pMapMarker(fr);
  }
  PLAYER.raf=requestAnimationFrame(pLoop);
}

function pSetupMaster(){
  const m=PLAYER.master;if(!m)return;
  m.onloadedmetadata=()=>{
    $("#pseek").max=Math.floor((m.duration||0)*1000)||1000;
    if(PLAYER.initialSeek!=null){m.currentTime=PLAYER.initialSeek;pSlaves(v=>v.currentTime=PLAYER.initialSeek);}
  };
  m.onplay=()=>{pSlaves(v=>v.play().catch(()=>{}));$("#pplay").textContent="⏸";PLAYER.raf=requestAnimationFrame(pLoop);};
  m.onpause=()=>{pSlaves(v=>v.pause());$("#pplay").textContent="▶";cancelAnimationFrame(PLAYER.raf);};
}

function pUpdateTelControls(c){
  const hasT=!!(PLAYER.tele&&PLAYER.tele.frame_count);
  const hasGps=PLAYER.gpsPts.length>0;
  $("#tc_hud").classList.toggle("hidden",!hasT);
  $("#tc_nerd").classList.toggle("hidden",!hasT&&!PLAYER.event);
  $("#tc_map").classList.toggle("hidden",!hasGps);
  $("#hud").style.display=(hasT&&$("#t_hud").checked)?"flex":"none";
}

async function openClip(c){
  pClearStage();
  PLAYER.cid=c.id;
  const wrap=el("div","player");
  const bar=el("div","bar");
  bar.innerHTML=`<b>${c.timestamp.replace("_"," ")}</b>
    <span class="note" id="pstatus">lädt…</span>
    <label class="tc hidden" id="tc_hud"><input type="checkbox" id="t_hud" checked> HUD</label>
    <label class="tc hidden" id="tc_map"><input type="checkbox" id="t_map"> Karte</label>
    <label class="tc hidden" id="tc_nerd"><input type="checkbox" id="t_nerd"> Debug</label>
    <select id="prate" class="ratesel"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option></select>
    <button class="btn sm ghost" id="pfull">⛶</button>
    <button class="x" id="pclose">✕</button>`;
  wrap.append(bar);
  const stage=el("div","stage");
  const grid=el("div","grid");stage.append(grid);
  const mapbox=el("div","mapbox hidden");mapbox.id="pmapbox";
  mapbox.innerHTML=`<div id="pmap"></div>`;stage.append(mapbox);
  const hud=el("div","hud");hud.id="hud";
  hud.innerHTML=`
    <div class="h-item h-turn" id="h-l">◀</div>
    <div class="h-item h-gear" id="h-gear">–</div>
    <div class="h-item h-speed"><span id="h-spd">0</span><small>km/h</small></div>
    <div class="h-item h-wheel" id="h-wheel">🎡</div>
    <div class="h-item h-accel"><div class="h-accel-fill" id="h-accel-fill"></div></div>
    <div class="h-item h-brake" id="h-brake">🛑</div>
    <div class="h-item h-ap" id="h-ap" style="display:none">AP</div>
    <div class="h-item h-turn" id="h-r">▶</div>`;
  stage.append(hud);
  const nerd=el("pre","nerd");nerd.id="pnerd";stage.append(nerd);
  wrap.append(stage);
  const transport=el("div","transport");
  transport.innerHTML=`<button class="btn sm" id="pplay">▶</button>
    <input type="range" id="pseek" value="0" min="0" max="1000">
    <span class="note" id="ptime">0:00 / 0:00</span>`;
  wrap.append(transport);
  document.body.append(wrap);
  $("#pclose").onclick=()=>{pClearStage();wrap.remove();};
  document.addEventListener("keydown",function esc(e){
    if(!document.body.contains(wrap)){document.removeEventListener("keydown",esc);return;}
    if(e.key==="Escape"){pClearStage();wrap.remove();document.removeEventListener("keydown",esc);}
    else if(e.key===" "){e.preventDefault();PLAYER.master&&(PLAYER.master.paused?PLAYER.master.play():PLAYER.master.pause());}
    else if(e.key==="ArrowRight"&&PLAYER.master){PLAYER.master.currentTime+=5;pSlaves(v=>v.currentTime=PLAYER.master.currentTime);}
    else if(e.key==="ArrowLeft"&&PLAYER.master){PLAYER.master.currentTime-=5;pSlaves(v=>v.currentTime=PLAYER.master.currentTime);}
  });
  $("#pfull").onclick=()=>{document.fullscreenElement?document.exitFullscreen():wrap.requestFullscreen();};
  $("#pplay").onclick=()=>{if(!PLAYER.master)return;PLAYER.master.paused?PLAYER.master.play():PLAYER.master.pause();};
  $("#pseek").oninput=()=>{if(!PLAYER.master)return;const t=$("#pseek").value/1000;PLAYER.master.currentTime=t;pSlaves(v=>v.currentTime=t);};
  $("#prate").onchange=()=>{if(!PLAYER.master)return;const r=+$("#prate").value;PLAYER.master.playbackRate=r;pSlaves(v=>v.playbackRate=r);};
  $("#t_hud").onchange=()=>pUpdateTelControls(c);
  $("#t_map").onchange=()=>pShowMap($("#t_map").checked);

  // event: fetch event.json seek offset if this clip has one
  if(c.has_event){
    try{PLAYER.event=await jget("api/event?id="+encodeURIComponent(c.id));}catch(e){PLAYER.event=null;}
    if(PLAYER.event&&PLAYER.event.seek!=null)PLAYER.initialSeek=PLAYER.event.seek;
    if(PLAYER.event&&PLAYER.event.lat&&PLAYER.event.lon)PLAYER.gpsPts=[[PLAYER.event.lat,PLAYER.event.lon]];
  }

  const status=$("#pstatus");
  let res;
  try{res=await jpost("api/prepare",{id:c.id});}catch(e){status.textContent="✗ Verbindungsfehler";return;}
  if(!res||!res.cameras){status.textContent="✗ "+(res&&res.error?res.error:"Fehler");return;}
  status.remove();

  let any=false;
  CAMS.forEach(([cam,label,area])=>{
    const info=res.cameras[cam];
    const tile=el("div","tile "+area);
    if(info&&(info.state==="ready"||info.state==="plain")){
      const v=document.createElement("video");v.controls=false;v.playsInline=true;v.muted=true;v.preload="auto";
      v.src=info.url;
      tile.append(v);
      const ctl=el("div","tilectl");
      const dl=el("a","iconbtn dlcam","⬇");dl.href=info.url;dl.download=cam+".mp4";dl.title="Kamera herunterladen";
      const fs=el("button","iconbtn fscam","⛶");fs.title="Vollbild";fs.onclick=(e)=>{e.stopPropagation();(v.requestFullscreen||v.webkitEnterFullscreen||function(){}).call(v);};
      ctl.append(dl,fs);tile.append(ctl);
      tile.append(el("div","tilelabel",label));
      grid.append(tile);
      PLAYER.videos.push(v);
      if(!PLAYER.master)PLAYER.master=v;
      any=true;
    }else{
      tile.classList.add("empty");
      if(info&&info.state==="locked")tile.innerHTML=`<div class="ic">🔒</div><div class="note">${label}</div>`;
      grid.append(tile);
    }
  });
  if(!any){grid.append(el("div","note","Kein Video verfügbar – kein Schlüssel für diesen Clip."));return;}
  if(res.errors&&res.errors.length)toast("Fehler: "+res.errors.join(", "));

  pSetupMaster();

  // telemetry (HUD/map): prepare() may have just extracted it, so build the
  // URL directly rather than relying on the (pre-prepare) clip list snapshot
  const telUrl="media/"+encodeURI(c.folder+"/"+c.timestamp+"-front.telemetry.json");
  try{const t=await jget(telUrl);if(t&&t.frame_count)PLAYER.tele=t;}catch(e){}
  if(PLAYER.tele&&PLAYER.tele.frames){
    const pts=PLAYER.tele.frames.filter(f=>f.lat&&f.lon).map(f=>[f.lat,f.lon]);
    if(pts.length)PLAYER.gpsPts=pts;
  }
  pUpdateTelControls(c);
  if(PLAYER.gpsPts.length)$("#tc_map").classList.remove("hidden");
}

/* ---------------- Dateien ---------------- */
async function viewFiles(m,path){
  m.innerHTML="";
  m.append(el("h2","title","Dateien"));
  const crumbs=el("div","crumbs");m.append(crumbs);
  const bar=el("div","saverow");
  const up=el("label","btn sm","⬆️ Hochladen");const fin=el("input");fin.type="file";fin.className="hidden";fin.multiple=true;up.append(fin);
  const mk=el("button","btn sm ghost","➕ Ordner");
  bar.append(up,mk);m.append(bar);
  const list=el("div","filelist");m.append(list);
  function crumbLinks(){
    crumbs.innerHTML="";
    const root=el("a",null,"🏠");root.onclick=()=>viewFiles($("#main"),"");crumbs.append(root);
    let acc="";path.split("/").filter(Boolean).forEach(seg=>{acc+=(acc?"/":"")+seg;const a=el("a",null,"/ "+seg);const cur=acc;a.onclick=()=>viewFiles($("#main"),cur);crumbs.append(a);});
  }
  crumbLinks();
  let data;try{data=await jget("api/files?path="+encodeURIComponent(path));}catch(e){return;}
  (data.entries||[]).sort((a,b)=>(b.dir-a.dir)||a.name.localeCompare(b.name)).forEach(ent=>{
    const it=el("div","fitem");
    const rel=(path?path+"/":"")+ent.name;
    it.append(el("div","ic",ent.dir?"📁":ent.image?"🖼️":ent.audio?"🎵":"📄"));
    const nm=el("div","nm",ent.name);it.append(nm);
    it.append(el("div","sz",ent.dir?"":human(ent.size)));
    const act=el("div","act");
    if(!ent.dir){const dl=el("button","iconbtn","⬇️");dl.title="Download";dl.onclick=e=>{e.stopPropagation();location.href="api/files/download?path="+encodeURIComponent(rel);};act.append(dl);}
    if(ent.audio&&rel.replace(/\\/g,"/").startsWith("Boombox/")){
      const lc=el("button","iconbtn","🔔");lc.title="Als LockChime festlegen (überschreibt Boombox/LockChime.wav)";
      lc.onclick=async e=>{e.stopPropagation();if(confirm("„"+ent.name+"“ als LockChime festlegen? Überschreibt Boombox/LockChime.wav.")){
        const r=await jpost("api/files/lockchime",{path:rel});
        if(r.ok){toast("LockChime gesetzt: "+ent.name);viewFiles($("#main"),path);}else{toast("✗ "+(r.error||"Fehler"));}
      }};
      act.append(lc);
    }
    const rn=el("button","iconbtn","✏️");rn.title="Umbenennen";rn.onclick=async e=>{e.stopPropagation();const n=prompt("Neuer Name",ent.name);if(n){await jpost("api/files/rename",{path:rel,name:n});viewFiles($("#main"),path);}};
    const del=el("button","iconbtn","🗑️");del.title="Löschen";del.onclick=async e=>{e.stopPropagation();if(confirm("Löschen: "+ent.name+"?")){await jpost("api/files/delete",{path:rel});viewFiles($("#main"),path);}};
    act.append(rn,del);it.append(act);
    it.onclick=()=>{if(ent.dir)viewFiles($("#main"),rel);else if(ent.image)lightbox(rel,ent.name);else if(ent.audio)audioPlayer(rel,ent.name);};
    list.append(it);
  });
  mk.onclick=async()=>{const n=prompt("Ordnername");if(n){await jpost("api/files/mkdir",{path:(path?path+"/":"")+n});viewFiles($("#main"),path);}};
  fin.onchange=async()=>{
    for(const f of fin.files){
      toast("Lade "+f.name+"…");
      await api("api/files/upload?path="+encodeURIComponent(path)+"&name="+encodeURIComponent(f.name),{method:"PUT",body:f});
    }
    toast("Upload fertig");viewFiles($("#main"),path);
  };
}
function lightbox(rel,name){
  const lb=el("div","lightbox");
  lb.append(el("div","cap",name));
  const img=new Image();img.src="api/files/download?inline=1&path="+encodeURIComponent(rel);lb.append(img);
  lb.onclick=e=>{if(e.target===lb)lb.remove();};
  document.addEventListener("keydown",function esc(e){if(e.key==="Escape"){lb.remove();document.removeEventListener("keydown",esc);}});
  document.body.append(lb);
}
function audioPlayer(rel,name){
  document.querySelectorAll(".audiobar").forEach(x=>x.remove());
  const bar=el("div","audiobar");
  bar.innerHTML=`<span class="ic">🎵</span><span class="cap"></span>
    <audio controls autoplay></audio>
    <button class="x">✕</button>`;
  bar.querySelector(".cap").textContent=name;
  bar.querySelector("audio").src="api/files/download?inline=1&path="+encodeURIComponent(rel);
  bar.querySelector(".x").onclick=()=>bar.remove();
  document.body.append(bar);
}
function human(b){b=+b||0;const u=["B","KB","MB","GB"];let i=0;while(b>=1024&&i<3){b/=1024;i++;}return b.toFixed(i?1:0)+" "+u[i];}

/* ---------------- Diagnose ---------------- */
async function viewDiag(m){
  m.append(el("h2","title","Diagnose"));
  const stats=el("div","stats");m.append(stats);
  const logcard=el("div","card");m.append(logcard);
  let s;try{s=await jget("api/status");}catch(e){return;}
  const d=s.diag||{};
  const items=[["Temperatur",d.temp||"–"],["Uptime",d.uptime||"–"],["WLAN",d.wifi_ssid||"–"],
    ["USB am Auto",d.gadget_active?"aktiv":"—"],["Clips",s.clips],["verschlüsselt",s.encrypted]];
  items.forEach(([k,v])=>{const c=el("div","stat");c.append(el("div","k",k));c.append(el("div","v",String(v)));stats.append(c);});
  const actions=el("div","saverow");
  const rb=el("button","btn sm","♻️ Neustart");rb.onclick=async()=>{if(confirm("Pi neu starten?")){await jpost("api/reboot",{});toast("Startet neu…");}};
  const td=el("button","btn sm ghost","🔀 Laufwerke togglen");td.onclick=async()=>{await jpost("api/toggle_drives",{});toast("getoggelt");};
  actions.append(rb,td);m.insertBefore(actions,logcard);
  logcard.append(el("h3",null,"Logs"));
  const sel=el("select");["archiveloop","setup","sync","retention"].forEach(w=>{const o=el("option",null,w);o.value=w;sel.append(o);});
  const box=el("div","logbox","lädt…");
  const load=async()=>{const r=await jget("api/log?which="+sel.value);box.textContent=r.text||"(leer)";box.scrollTop=box.scrollHeight;};
  sel.onchange=load;const f=el("div","field");f.append(sel);logcard.append(f,box);load();
}

/* ---------------- Einstellungen ---------------- */
function fld(label,id,type,val,ph){return `<div class="field"><label>${label}</label><input id="${id}" type="${type||'text'}" value="${val==null?'':String(val).replace(/"/g,'&quot;')}" ${ph?`placeholder="${ph}"`:''}></div>`;}
function chk(label,id,on){return `<label class="checkline"><input type="checkbox" id="${id}" ${on?'checked':''}> ${label}</label>`;}
async function viewSettings(m){
  m.append(el("h2","title","Einstellungen"));
  let c;try{c=await jget("api/settings");}catch(e){return;}
  let login={logged_in:false,has_refresh:false};
  try{login=(await jget("api/status")).login||login;}catch(e){}
  const box=el("div");box.innerHTML=`
    <div class="card"><h3>Tesla-Konto (für Schlüssel-Abruf)</h3>
      <div class="note">Damit verschlüsselte Aufnahmen automatisch entschlüsselt werden können, muss der Hub sich einmalig bei Tesla anmelden und einen Schlüssel-Abruf-Token holen. Ohne diesen Login bleiben verschlüsselte Clips dauerhaft gesperrt.</div>
      <div class="saverow"><span class="note" id="teslastatus">${login.logged_in?"✓ eingeloggt"+(login.has_refresh?" (bleibt automatisch gültig)":""):"✗ nicht eingeloggt"}</span></div>
      <div class="saverow"><button class="btn sm" id="teslaloginbtn">Bei Tesla einloggen</button></div>
      <div class="note">Öffnet die Tesla-Anmeldeseite in einem neuen Tab. Nach dem Login zeigt der Browser eine leere/Fehler-Seite unter <code>dashcam.tesla.com/callback?...</code> — die komplette Adresse aus der Adresszeile hier einfügen:</div>
      ${fld("Callback-URL nach Login","s_tesla_callback","text","","https://dashcam.tesla.com/callback?code=...")}
      <div class="saverow"><button class="btn sm ghost" id="teslaexchange">Bestätigen</button><span class="note" id="teslamsg"></span></div>
    </div>
    <div class="card"><h3>Verbindung / NAS</h3>
      ${fld("Archiv-Server (NAS-IP)","s_archive_server","text",c.archive_server)}
      ${fld("Share + Pfad","s_share_name","text",c.share_name)}
      ${fld("Benutzer","s_share_user","text",c.share_user)}
      ${fld("Passwort","s_share_password","password","",c.share_password_set?"•••• unverändert":"")}
      <div class="saverow"><button class="btn sm" id="nastest">Verbindung testen</button><span class="note" id="nasmsg"></span></div>
      ${chk("RecentClips archivieren","s_archive_recentclips",c.archive_recentclips==='true')}
      ${chk("SavedClips archivieren","s_archive_savedclips",c.archive_savedclips==='true')}
      ${chk("SentryClips archivieren","s_archive_sentryclips",c.archive_sentryclips==='true')}
    </div>
    <div class="card"><h3>Netzwerk</h3>
      ${fld("WLAN-SSID","s_ssid","text",c.ssid)}
      ${fld("WLAN-Passwort","s_wifipass","password","",c.wifipass_set?"•••• unverändert":"")}
      ${fld("Access-Point SSID","s_ap_ssid","text",c.ap_ssid)}
      ${fld("Access-Point Passwort","s_ap_pass","password","",c.ap_pass_set?"•••• unverändert":"")}
    </div>
    <div class="card"><h3>Auto wachhalten / BLE</h3>
      ${fld("TeslaFi API-Token","s_teslafi_api_token","password","",c.teslafi_api_token_set?"•••• gesetzt":"")}
      ${fld("Tessie API-Token","s_tessie_api_token","password","",c.tessie_api_token_set?"•••• gesetzt":"")}
      ${fld("BLE Fahrzeug-VIN","s_tesla_ble_vin","text",c.tesla_ble_vin)}
      <div class="saverow"><button class="btn sm ghost" id="blepair">BLE koppeln</button><span class="note" id="blemsg"></span></div>
    </div>
    <div class="card"><h3>Benachrichtigungen</h3>
      ${chk("Pushover aktiv","s_pushover_enabled",c.pushover_enabled==='true')}
      ${fld("Pushover User-Key","s_pushover_user_key","password","",c.pushover_user_key_set?"•••• gesetzt":"")}
      ${fld("Pushover App-Key","s_pushover_app_key","password","",c.pushover_app_key_set?"•••• gesetzt":"")}
      ${chk("Telegram aktiv","s_telegram_enabled",c.telegram_enabled==='true')}
      ${fld("Telegram Chat-ID","s_telegram_chat_id","text",c.telegram_chat_id)}
      ${fld("Telegram Bot-Token","s_telegram_bot_token","password","",c.telegram_bot_token_set?"•••• gesetzt":"")}
    </div>
    <div class="card"><h3>Aufbewahrung & Sync</h3>
      ${chk("Music/LightShow/Boombox automatisch bei WLAN synchronisieren","s_sync_all_content",c.sync_all_content==='true')}
      ${fld("Sync-Pfad auf dem NAS (Share + Unterpfad)","s_sync_media_path","text",c.sync_media_path,"Tesla_Video/Sonstiges")}
      <div class="note">Legt darin automatisch die Ordner <code>Music/</code>, <code>LightShow/</code>, <code>Boombox/</code> an (gleicher NAS-Server/Zugang wie oben bei „Verbindung / NAS"). Ohne Pfad passiert nichts.</div>
      <div class="saverow"><button class="btn sm ghost" id="mediasync">Jetzt synchronisieren</button><span class="note" id="mediasyncmsg"></span></div>
      <div class="field"><label>Aufnahmen auf dem Stick löschen</label>
        <select id="s_retention_mode">
          <option value="off"${c.retention_mode==='off'||!c.retention_mode?' selected':''}>Aus</option>
          <option value="time"${c.retention_mode==='time'?' selected':''}>Nach Zeitraum</option>
          <option value="space"${c.retention_mode==='space'?' selected':''}>Rollierend nach Speicher</option>
        </select></div>
      ${fld("Aufbewahrung (Tage)","s_retention_days","number",c.retention_days)}
      ${fld("Mind. freien Speicher halten (GB)","s_retention_free_gb","number",c.retention_free_gb)}
      <div class="note">Gelöscht wird nur, was bereits auf dem NAS gesichert ist.</div>
    </div>
    <div class="card"><h3>Sicherheit & System</h3>
      ${chk("SSH-Passwort-Login abschalten","s_ssh_disable_password",c.ssh_disable_password==='true')}
      ${fld("Tresor automatisch sperren nach (Min, 0=aus)","s_vault_autolock_min","number",c.vault_autolock_min)}
      ${fld("Zeitzone","s_time_zone","text",c.time_zone,"Europe/Berlin")}
      ${fld("Hostname","s_teslausb_hostname","text",c.teslausb_hostname)}
    </div>
    <div class="saverow"><button class="btn primary" style="width:auto" id="savebtn">Speichern</button><span class="note" id="savemsg"></span></div>`;
  m.append(box);
  $("#teslaloginbtn").onclick=async()=>{
    $("#teslamsg").textContent="hole Login-Link…";
    try{const r=await jget("api/tesla/login_url");window.open(r.url,"_blank");$("#teslamsg").textContent="Tab geöffnet – nach Login die Adresse hier einfügen.";}
    catch(e){$("#teslamsg").textContent="✗ Fehler beim Abrufen des Login-Links";}
  };
  $("#teslaexchange").onclick=async()=>{
    const cb=$("#s_tesla_callback").value.trim();
    if(!cb){$("#teslamsg").textContent="Bitte zuerst die Callback-URL einfügen";return;}
    $("#teslamsg").textContent="prüfe…";
    try{
      const r=await jpost("api/tesla/exchange",{callback:cb});
      if(r.ok){$("#teslamsg").textContent="✓ eingeloggt";$("#teslastatus").textContent="✓ eingeloggt"+(r.refresh?" (bleibt automatisch gültig)":"");toast("Tesla-Login erfolgreich");}
      else{$("#teslamsg").textContent="✗ "+(r.error||"Fehler");}
    }catch(e){$("#teslamsg").textContent="✗ Verbindungsfehler";}
  };
  $("#nastest").onclick=async()=>{$("#nasmsg").textContent="Teste…";
    const r=await jget("api/nas/test");$("#nasmsg").textContent=r.ok?("✓ OK"+(r.writable?" (schreibbar)":" (nur lesbar)")):("✗ "+(r.error||"Fehler"));};
  $("#blepair").onclick=async()=>{$("#blemsg").textContent="Koppeln…";const r=await jpost("api/ble/pair",{});$("#blemsg").textContent=r.ok?"✓ ok":"✗ Fehler";};
  $("#mediasync").onclick=async()=>{
    $("#mediasyncmsg").textContent="synchronisiere…";
    let before=0;try{before=(await jget("api/nas/media_status")).t||0;}catch(e){}
    await jpost("api/nas/sync_media",{});
    const poll=async()=>{
      let st;try{st=await jget("api/nas/media_status");}catch(e){return;}
      if(!st.t||st.t<=before){setTimeout(poll,1500);return;}
      $("#mediasyncmsg").textContent=st.ok?`✓ fertig (${st.copied}/3 Ordner)`:"✗ "+(st.error||"Fehler");
    };
    setTimeout(poll,2000);
  };
  $("#savebtn").onclick=async()=>{
    const fields=["archive_server","share_name","share_user","ssid","ap_ssid","tesla_ble_vin",
      "telegram_chat_id","retention_days","retention_free_gb","vault_autolock_min","time_zone","teslausb_hostname","sync_media_path"];
    const secrets=["share_password","wifipass","ap_pass","teslafi_api_token","tessie_api_token",
      "pushover_user_key","pushover_app_key","telegram_bot_token"];
    const bools=["archive_recentclips","archive_savedclips","archive_sentryclips","sync_all_content",
      "ssh_disable_password","pushover_enabled","telegram_enabled"];
    const body={};
    fields.forEach(f=>body[f]=($("#s_"+f)||{}).value||"");
    secrets.forEach(f=>{const v=($("#s_"+f)||{}).value;if(v)body[f]=v;});
    bools.forEach(f=>body[f]=($("#s_"+f)||{}).checked||false);
    body.retention_mode=$("#s_retention_mode").value;
    $("#savemsg").textContent="Speichern…";
    const r=await jpost("api/settings",body);
    $("#savemsg").textContent=r.ok?"✓ gespeichert (greift nach Archiv-Neustart/Reboot)":"✗ "+(r.error||"Fehler");
    if(r.ok)toast("Gespeichert");
  };
}

boot();
