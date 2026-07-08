const $=s=>document.querySelector(s);
const CAMS=[{k:"front",a:"a-front",l:"Front"},{k:"back",a:"a-back",l:"Rear"},
  {k:"left_repeater",a:"a-left",l:"Left"},{k:"right_repeater",a:"a-right",l:"Right"},
  {k:"left_pillar",a:"a-lp",l:"Pillar L"},{k:"right_pillar",a:"a-rp",l:"Pillar R"}];
let allClips=[], videos=[], master=null, raf=0, tele=null, activeId=null, curEvent=null;
let lmap=null, lline=null, lmark=null, gpsPts=[];
let initialSeek=null;
let gpsFilter=null, _gpsMarkers=[];

const BM="(async()=>{const pick=document.createElement('input');pick.type='file';pick.accept='application/json,.json';pick.onchange=async()=>{try{const job=JSON.parse(await pick.files[0].text());const items=job.items||job;let raw=sessionStorage.getItem('ROCP_token'),token=raw;try{const p=JSON.parse(raw);token=(typeof p==='string')?p:(p.access_token||p.token||p.accessToken||raw);}catch(e){}if(!token){alert('No Tesla token – log in to dashcam.tesla.com first.');return;}const out=[],CH=30;for(let i=0;i<items.length;i+=CH){const r=await fetch('/api/1/decrypt/batch',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token,'Accept':'application/json'},body:JSON.stringify({items:items.slice(i,i+CH)})});if(!r.ok){alert('API error '+r.status+' at chunk '+i);return;}const j=await r.json();(j.results||[]).forEach(x=>{if(x.key)out.push({id:x.id,key:x.key});});}const blob=new Blob([JSON.stringify({results:out})],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='keys.json';a.click();alert('Done: '+out.length+' keys -> keys.json');}catch(e){alert('Error: '+e.message);}};pick.click();})();";

// ---------- Status ----------
async function refreshStatus(){
  try{
    const s=await fetch("api/status").then(r=>r.json());
    $("#status").innerHTML=`🎞️ <b>${s.clips}</b> Clips · 🔒 <b>${s.encrypted}</b> · 🔑 <b>${s.keyed}</b> · ✅ <b>${s.decrypted}</b> · ⏳ no key <b>${s.need_keys}</b>`+(s.busy?" · running…":"");
    const li=s.login||{};
    $("#lpill").className="pill "+(li.logged_in?"ok":"bad"); $("#lpill").textContent=li.logged_in?(li.has_refresh?"logged in ✓":"logged in"):"not logged in";
    $("#loginbox").style.display=li.logged_in?"none":"block";
    let api=s.last_api||{}; if(api.ok===false) $("#lmsg").textContent="Direct API: "+api.msg; else if(api.ok&&api.got) $("#lmsg").textContent="Direct API: "+api.got+" new keys.";
    $("#tstat").textContent=`Clips ${s.clips} · encrypted ${s.encrypted} · with key ${s.keyed} · no key ${s.need_keys}`;
    return s;
  }catch(e){return null;}
}

// ---------- Clip browser ----------
async function loadClips(keepActive){
  if(!keepActive) $("#cliplist").innerHTML='<div class="loading">⏳ Loading clips…</div>';
  allClips=await fetch("api/clips").then(r=>r.json()).catch(()=>[]);
  buildSidebar();
  if(keepActive&&activeId){const c=allClips.find(x=>x.id===activeId); if(c) markActive(activeId);}
}
function clipState(c){ return c.has_locked&&!c.playable&&!c.needs_prepare ? "locked" : (c.needs_prepare?"key":"ready"); }
let _collapsed={};
const _lockSvg=(color,title)=>`<svg title="${title}" width="12" height="14" viewBox="0 0 24 28" style="vertical-align:middle"><path d="M6 12V8a6 6 0 1 1 12 0v4h1a3 3 0 0 1 3 3v8a3 3 0 0 1-3 3H5a3 3 0 0 1-3-3v-8a3 3 0 0 1 3-3h1z" fill="${color}"/></svg>`;
function _lockBadge(c){
  if(!c.has_locked && !c.needs_prepare) return "";
  if(c.needs_prepare) return _lockSvg("#34d399","encrypted – key available");
  return _lockSvg("#9aa7b4","encrypted – no key");
}
function _clipRow(c){
  const r=document.createElement("div"); r.className="cliprow"+(c.id===activeId?" active":""); r.dataset.id=c.id;
  const badges=(c.has_tel?'<span title="Telemetry/HUD available">📊</span>':"")+(c.has_event?'<span title="Event present – player jumps to event">📅</span>':"")+_lockBadge(c);
  const tm=c.timestamp.replace("_"," ").replace(/-/g,(m,i)=>i>9?":":"-");
  r.innerHTML=`<img class="thumb" loading="lazy" src="api/thumb?id=${encodeURIComponent(c.id)}" onerror="this.classList.add('noimg');this.removeAttribute('src')"><span class="cmid"><span class="cliptime">${tm}</span><span class="badges">${badges}</span></span>`;
  r.onclick=()=>open(c);
  return r;
}
function buildSidebar(){
  const q=$("#search").value.trim().toLowerCase(), fDrive=$("#filterDrive").checked, fEvent=$("#filterEvent").checked, fHonk=$("#filterHonk").checked;
  const filtered=[];
  for(const c of allClips){
    if(fDrive && !c.has_tel) continue;
    if(fEvent && !c.has_event) continue;
    if(fHonk && c.reason!=="user_interaction_honk") continue;
    if(q && !(c.timestamp.toLowerCase().includes(q) || (c.folder||"").toLowerCase().includes(q))) continue;
    if(gpsFilter){
      const hasPtInBounds = c.gps_bounds && gpsFilter.contains([c.gps_bounds.center_lat, c.gps_bounds.center_lon]);
      if(!hasPtInBounds) continue;
    }
    filtered.push(c);
  }
  filtered.sort((a,b)=>b.timestamp.localeCompare(a.timestamp));
  const el=$("#cliplist"); el.innerHTML="";
  const vehicles=new Set(filtered.map(c=>c.vehicle).filter(Boolean));
  if(vehicles.size>0){
    const groups={}; const ungrouped=[];
    for(const c of filtered){ if(c.vehicle)(groups[c.vehicle]||=[]).push(c); else ungrouped.push(c); }
    const sorted=[...vehicles].sort();
    for(const v of sorted){
      const head=document.createElement("div"); head.className="ghead";
      head.innerHTML=`<span>${(_collapsed[v]?"▸ ":"▾ ")+v}</span><span class="cnt">${groups[v].length}</span>`;
      head.onclick=()=>{_collapsed[v]=!_collapsed[v];buildSidebar();};
      el.appendChild(head);
      if(!_collapsed[v]) for(const c of groups[v]) el.appendChild(_clipRow(c));
    }
    for(const c of ungrouped) el.appendChild(_clipRow(c));
  } else {
    for(const c of filtered) el.appendChild(_clipRow(c));
  }
}
function markActive(id){ [...document.querySelectorAll(".cliprow")].forEach(r=>r.classList.toggle("active",r.dataset.id===id)); }

// ---------- Open / Playback ----------
function clearStage(){
  cancelAnimationFrame(raf); videos=[]; master=null; tele=null;
  [...$("#stage").querySelectorAll(".tile")].forEach(t=>t.remove());
  $("#stageaction").style.display="none"; $("#hud").style.display="none"; $("#nerd").style.display="none";
  $("#telctrl").style.display="none"; $("#telnone").style.display="none";
}
function showAction(html){const a=$("#stageaction");a.innerHTML=html;a.style.display="flex";}
function setupMaster(){
  master=videos[0]; if(!master) return;
  master.onloadedmetadata=()=>{$("#seek").max=Math.floor(master.duration*1000)||1000; if(initialSeek!==null){master.currentTime=initialSeek; slaves(v=>v.currentTime=initialSeek);}};
  master.onplay=()=>{slaves(v=>v.play().catch(()=>{}));$("#play").textContent="⏸";loop();};
  master.onpause=()=>{slaves(v=>v.pause());$("#play").textContent="▶";cancelAnimationFrame(raf);};
}
async function open(c, _prepared){
  activeId=c.id; markActive(c.id);
  $("#placeholder").style.display="none";
  clearStage(); $("#stage").style.display="grid"; $("#bar").style.display="none";
  $("#meta").textContent=(c.source?c.source+" · ":"")+c.timestamp.replace("_"," ");
  let anyPlay=false, anyKey=false, anyLocked=false;
  for(const cam of CAMS){
    const cm=c.cameras[cam.k];
    const t=document.createElement("div"); t.className="tile "+cam.a+(cm?"":" empty");
    const tag=document.createElement("span"); tag.className="tag"; tag.textContent=cam.l; t.appendChild(tag);
    if(cm&&cm.url){const v=document.createElement("video");v.src=cm.url;v.muted=true;v.playsInline=true;v.preload="auto";t.appendChild(v);videos.push(v);anyPlay=true;
      const dl=document.createElement("a");dl.className="dlcam";dl.href=cm.url;dl.download=cm.url.split("/").pop();dl.title="Download this camera";dl.textContent="⬇";t.appendChild(dl);
      const fs=document.createElement("button");fs.className="fscam";fs.title="Fullscreen";fs.textContent="⛶";fs.onclick=(ev)=>{ev.stopPropagation();const el=t.querySelector("video");if(el){if(el.requestFullscreen)el.requestFullscreen();else if(el.webkitEnterFullscreen)el.webkitEnterFullscreen();}};t.appendChild(fs);}
    else if(cm&&cm.state==="key"){anyKey=true;const o=document.createElement("div");o.className="camnote";o.textContent="🔒";t.appendChild(o);}
    else if(cm&&cm.state==="locked"){anyLocked=true;const o=document.createElement("div");o.className="camnote";o.textContent="🔒";t.appendChild(o);}
    $("#stage").appendChild(t);
  }
  // Telemetrie laden (falls vorhanden)
  tele = c.telemetry ? await fetch(c.telemetry).then(r=>r.json()).catch(()=>null) : null;
  buildGps();
  // Event-Daten laden (Seek, GPS-Fallback, Reason)
  initialSeek = null;
  curEvent = null;
  if(c.has_event){
    try{ curEvent=await fetch(`api/event?id=${encodeURIComponent(c.id)}`).then(r=>r.json()).catch(()=>null); }catch(e){}
    if(curEvent&&curEvent.seek>=0) initialSeek=curEvent.seek;
    if(!gpsPts.length && curEvent&&curEvent.lat&&curEvent.lon) gpsPts=[[curEvent.lat,curEvent.lon]];
  }
  if($("#t_map").checked) showMap(true);
  // key available -> decrypt transparently and play immediately
  if(anyKey && !_prepared){ return prepareAndOpen(c.id); }
  if(anyPlay){
    $("#bar").style.display="flex"; setupMaster(); applyHud(); updateTelControls();
    const frontCam=c.cameras.front;
    if(!tele && frontCam && frontCam.state==="plain"){
      fetch("api/prepare",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:c.id})})
        .then(r=>r.json()).then(async r=>{
          if(activeId!==c.id || !r || !r.clip || !r.clip.telemetry) return;
          const t=await fetch(r.clip.telemetry).then(r=>r.json()).catch(()=>null);
          if(t && activeId===c.id){ tele=t; buildGps(); applyHud(); updateTelControls(); }
        }).catch(()=>{});
    }
    return;
  }
  // encrypted WITHOUT key -> offer to fetch key and decrypt
  if(anyLocked){ showAction(`<div class="msg">🔒 Encrypted – no key yet.</div><button class="btn" id="getkey">🔑 Fetch key &amp; play</button><div class="msg" id="gkmsg"></div>`); $("#getkey").onclick=()=>fetchKeyAndOpen(c.id); }
  else if(anyKey){ showAction(`<div class="msg">⚠️ Decryption failed – try again in the 🔑 panel.</div>`); }
}
async function prepareAndOpen(id){
  showAction(`<div class="msg">🔓 Decrypting…</div>`);
  const r=await fetch("api/prepare",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id})}).then(r=>r.json()).catch(()=>({ok:false,error:"Network error"}));
  refreshStatus();
  if(r&&r.clip&&(r.ok||r.clip.playable)){ updateClip(r.clip); open(r.clip, true); }
  else if(r&&r.errors&&r.errors.length){ showAction(`<div class="msg">Error: ${r.errors.join(", ")}</div>`); }
  else { showAction(`<div class="msg">Error: ${(r&&r.error)||"unknown"}</div>`); }
}
async function fetchKeyAndOpen(id){
  $("#gkmsg") && ($("#gkmsg").textContent="Fetching key…");
  await fetch("api/fetch",{method:"POST"});
  const t=setInterval(async()=>{
    const s=await fetch("api/status").then(r=>r.json()).catch(()=>null);
    if(s&&!s.busy){ clearInterval(t); await loadClips(true);
      const fresh=allClips.find(c=>c.id===id);
      if(fresh&&(fresh.needs_prepare||fresh.playable)) open(fresh);
      else $("#gkmsg")&&($("#gkmsg").textContent="No key received – try via 🔑 (bookmarklet).");
    }
  },1500);
}
function updateClip(fresh){ const i=allClips.findIndex(c=>c.id===fresh.id); if(i>=0){allClips[i]=fresh; buildSidebar();} }

function slaves(fn){videos.forEach(v=>{if(v!==master)fn(v);});}
function loop(){
  const t=master.currentTime;
  slaves(v=>{if(Math.abs(v.currentTime-t)>0.12)v.currentTime=t;});
  $("#seek").value=Math.floor(t*1000); $("#time").textContent=fmt(t)+" / "+fmt(master.duration||0);
  if(tele&&tele.frame_count){const i=Math.min(tele.frame_count-1,Math.max(0,Math.round(t*tele.fps)));const fr=tele.frames[i];
    if($("#t_hud").checked)hud(fr);
    if($("#t_nerd").checked){nerd(fr);$("#nerd").style.display="block";}else $("#nerd").style.display="none";
    if($("#t_map").checked)mapMarker(fr);}
  raf=requestAnimationFrame(loop);
}
function hud(f){
  $("#h-gear").textContent=f.gear??"–"; $("#h-spd").textContent=Math.abs(Math.round(f.speed_kmh||0));
  $("#h-l").classList.toggle("on",!!f.blink_l); $("#h-r").classList.toggle("on",!!f.blink_r);
  const accelPct=Math.min(100,Math.max(0,f.accel||0)); $("#h-accel-fill").style.height=accelPct+"%";
  $("#h-brake").classList.toggle("on",!!f.brake); const ap=(f.autopilot||0)>0; $("#h-ap").classList.toggle("on",ap); $("#h-ap").style.display=ap?"flex":"none";
  $("#h-wheel").style.transform=`rotate(${f.steer||0}deg)`; $("#h-steer").classList.toggle("on",Math.abs(f.steer||0)>3);
}
function applyHud(){ $("#hud").style.display=(tele&&tele.frame_count&&$("#t_hud").checked)?"flex":"none"; }
function buildGps(){ gpsPts=(tele&&tele.frames)?tele.frames.filter(f=>f.lat&&f.lon).map(f=>[f.lat,f.lon]):[]; }
function ensureMap(){
  if(lmap||!window.L) return lmap;
  lmap=L.map("map",{attributionControl:false});
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:19}).addTo(lmap);
  return lmap;
}
function drawTrack(){
  if(!ensureMap()) return;
  if(lline){lmap.removeLayer(lline);lline=null;} if(lmark){lmap.removeLayer(lmark);lmark=null;}
  if(!gpsPts.length) return;
  lline=L.polyline(gpsPts,{color:"#3b82f6",weight:4}).addTo(lmap);
  lmark=L.circleMarker(gpsPts[0],{radius:6,color:"#fff",weight:2,fillColor:"#34d399",fillOpacity:1}).addTo(lmap);
  lmap.fitBounds(lline.getBounds(),{padding:[20,20]});
}
function showMap(on){
  $("#map").style.display=on?"block":"none";
  if(!on) return;
  if(!window.L){ $("#map").innerHTML='<div style="padding:14px;color:var(--muted)">Map unavailable (no internet for map library/tiles).</div>'; return; }
  drawTrack(); setTimeout(()=>lmap&&lmap.invalidateSize(),60);
}
function mapMarker(f){ if(lmark&&f&&f.lat&&f.lon) lmark.setLatLng([f.lat,f.lon]); }
function updateTelControls(){
  const hasTel=!!(tele&&tele.frame_count);
  const hasGps=gpsPts.length>0;
  const hasEvent=!!curEvent;
  const hasNerd=hasTel||hasEvent;
  const hasAny=hasTel||hasGps||hasEvent;
  $("#telctrl").style.display=hasAny?"inline-flex":"none";
  $("#telnone").style.display=hasAny?"none":"inline";
  $("#t_hud").parentElement.style.display=hasTel?"inline-flex":"none";
  $("#t_nerd").parentElement.style.display=hasNerd?"inline-flex":"none";
  $("#t_map").parentElement.style.display=hasGps?"inline-flex":"none";
  if(!hasTel){ $("#hud").style.display="none"; }
  if(!hasNerd || !$("#t_nerd").checked){ $("#nerd").style.display="none"; }
  if(hasEvent && !hasTel && $("#t_nerd").checked){ nerdEvent(); $("#nerd").style.display="block"; }
  if(!hasGps){ $("#map").style.display="none"; $("#t_map").checked=false; }
}
const CAM_LABELS=["Front","Rear","Left","Right","Pillar L","Pillar R"];
const REASON_LABELS={"sentry_aware_object_detection":"Object detected (Sentry)","sentry_aware_accel":"Acceleration (Sentry)","user_interaction_dashcam_icon_tapped":"Manual save","user_interaction_honk":"Honk","sentry_aware_intrusion":"Intrusion (Sentry)"};
function nerd(f){
  const bl=((f.blink_l?"◀":"")+(f.blink_r?"▶":""))||"–";
  let lines=[
    "t "+f.t+" s","Speed "+f.speed_kmh+" km/h","Gear "+(f.gear??"–"),
    "Steering "+(f.steer??"–")+"°","Throttle "+(f.accel??"–"),"Brake "+(f.brake?"ON":"–"),
    "Blinker "+bl,"Autopilot "+(f.autopilot??0),
    "GPS "+(f.lat??"–")+", "+(f.lon??"–"),"Heading "+(f.heading??"–")+"°"
  ];
  if(curEvent) lines.push("","— Event —",...eventNerdLines());
  $("#nerd").innerHTML=lines.map(x=>"<div>"+x+"</div>").join("");
}
function eventNerdLines(){
  if(!curEvent) return [];
  const e=curEvent, lines=[];
  if(e.reason) lines.push("Reason: "+(REASON_LABELS[e.reason]||e.reason));
  if(e.city||e.street) lines.push("Location: "+[e.street,e.city].filter(Boolean).join(", "));
  if(e.lat&&e.lon) lines.push("GPS: "+e.lat+", "+e.lon);
  if(e.camera!=null) lines.push("Camera: "+(CAM_LABELS[+e.camera]||e.camera));
  if(e.seek!=null) lines.push("Event @ "+Math.round(e.seek)+" s");
  return lines;
}
function nerdEvent(){
  $("#nerd").innerHTML=eventNerdLines().map(x=>"<div>"+x+"</div>").join("");
}
const fmt=s=>{s=Math.max(0,s|0);return (s/60|0)+":"+String(s%60).padStart(2,"0");};
$("#play").onclick=()=>master&&(master.paused?master.play():master.pause());
$("#seek").oninput=e=>{const t=e.target.value/1000;if(master){master.currentTime=t;slaves(v=>v.currentTime=t);}};
$("#rate").onchange=e=>{const r=+e.target.value;if(master){master.playbackRate=r;slaves(v=>v.playbackRate=r);}};
$("#full").onclick=()=>document.fullscreenElement?document.exitFullscreen():$("#content").requestFullscreen();
addEventListener("keydown",e=>{if(!master)return;
  if(e.code==="Space"){e.preventDefault();master.paused?master.play():master.pause();}
  if(e.code==="ArrowRight"){const t=master.currentTime+5;master.currentTime=t;slaves(v=>v.currentTime=t);}
  if(e.code==="ArrowLeft"){const t=Math.max(0,master.currentTime-5);master.currentTime=t;slaves(v=>v.currentTime=t);}});

// ---------- GPS filter (marker map + rectangle select) ----------
let _gpsMap=null, _mapReady=false, _drawMode=false, _drawRect=null, _drawStart=null;
const _dotIcon=()=>L.divIcon({className:'',html:'<div style="width:8px;height:8px;border-radius:50%;background:#3b82f6;border:1px solid #fff;opacity:.85"></div>',iconSize:[8,8],iconAnchor:[4,4]});
const _dotActiveIcon=()=>L.divIcon({className:'',html:'<div style="width:10px;height:10px;border-radius:50%;background:#34d399;border:2px solid #fff"></div>',iconSize:[10,10],iconAnchor:[5,5]});
async function initMapFilter(){
  if(_mapReady) return;
  if(!window.L) return;
  try {
    _mapReady=true;
    gpsFilter=null;
    const gpsMap=L.map("mapFilter",{attributionControl:false,zoomControl:false});
    _gpsMap=gpsMap;
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",{maxZoom:19,attribution:''}).addTo(gpsMap);
    const s=await fetch("api/all_gps").then(r=>r.json()).catch(()=>({points:[]}));
    const pts=s.points||[];
    if(pts.length){
      const icon=_dotIcon();
      for(const p of pts){
        const m=L.marker([p[0],p[1]],{icon}).addTo(gpsMap);
        m._clipId=p[2];
        m.on("click",()=>{const c=allClips.find(x=>x.id===p[2]); if(c) open(c);});
        _gpsMarkers.push(m);
      }
      gpsMap.setView([pts[0][0],pts[0][1]],14);
    }
    function enterDraw(){ _drawMode=true; gpsMap.dragging.disable(); $("#drawRectBtn").classList.add("active"); $("#drawHint").textContent="Click & drag to select area"; }
    function exitDraw(){ _drawMode=false; gpsMap.dragging.enable(); $("#drawRectBtn").classList.remove("active"); $("#drawHint").textContent=""; }
    $("#drawRectBtn").onclick=()=>{ _drawMode ? exitDraw() : enterDraw(); };
    gpsMap.on("mousedown",e=>{
      if(!_drawMode) return;
      _drawStart=e.latlng;
      if(_drawRect){ gpsMap.removeLayer(_drawRect); _drawRect=null; }
    });
    gpsMap.on("mousemove",e=>{
      if(!_drawMode||!_drawStart) return;
      const b=L.latLngBounds(_drawStart,e.latlng);
      if(_drawRect) _drawRect.setBounds(b);
      else { _drawRect=L.rectangle(b,{color:"#3b82f6",weight:2,fillOpacity:0.15}).addTo(gpsMap); }
    });
    gpsMap.on("mouseup",e=>{
      if(!_drawMode||!_drawStart) return;
      const b=L.latLngBounds(_drawStart,e.latlng);
      _drawStart=null;
      if(b.getNorthEast().equals(b.getSouthWest())) return;
      if(_drawRect) _drawRect.setBounds(b);
      gpsFilter=b;
      exitDraw();
      buildSidebar();
    });
    $("#filterResetBtn").onclick=()=>{
      gpsFilter=null; if(_drawRect){_gpsMap.removeLayer(_drawRect);_drawRect=null;} exitDraw(); buildSidebar();
    };
  } catch(e) { _mapReady=false; console.error("Map init failed:", e); }
}

// ---------- Tools (fetch keys) ----------
async function onKeyFile(e){
  const f=e.target.files[0]; if(!f) return; $("#msg").textContent="Uploading…";
  let data; try{ data=JSON.parse(await f.text()); }catch(err){ $("#msg").textContent="Invalid JSON."; return; }
  const r=await fetch("api/keys",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)}).then(r=>r.json()).catch(()=>({ok:false}));
  $("#msg").textContent=r.ok?`${r.stored} keys saved.`:"Error saving.";
  if(r.ok){ refreshStatus(); loadClips(true); }
}
async function boot(){
  $("#bmlink").href="javascript:"+BM;
  $("#toolsbtn").onclick=()=>$("#tools").style.display="flex";
  $("#toolsx").onclick=()=>$("#tools").style.display="none";
  $("#copybm").onclick=async()=>{try{await navigator.clipboard.writeText(BM);$("#copybm").textContent="copied ✓";setTimeout(()=>$("#copybm").textContent="Copy snippet",1500);}catch(e){alert("Copy failed.");}};
  $("#keyfile").onchange=onKeyFile;
  try{const {url}=await fetch("api/login/url").then(r=>r.json());$("#loginlink").href=url;$("#loginurl").value=url;}catch(e){}
  $("#cbgo").onclick=async()=>{
    const r=await fetch("api/login/exchange",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({callback:$("#cb").value.trim()})}).then(r=>r.json());
    $("#lmsg").textContent=r.ok?("Login OK"+(r.refresh?" – Refresh token received.":".")):("Error: "+r.error); refreshStatus();
  };
  $("#fetchbtn").onclick=async()=>{$("#lmsg").textContent="Fetching keys…";await fetch("api/fetch",{method:"POST"});setTimeout(()=>{refreshStatus();loadClips(true);},4000);};
  $("#thumbsbtn").onclick=async()=>{
    $("#thumbsbtn").disabled=true; $("#thumbmsg").textContent="Generating…";
    await fetch("api/thumbs_all",{method:"POST"});
    const poll=setInterval(async()=>{
      const s=await fetch("api/status").then(r=>r.json()).catch(()=>null);
      if(s&&s.thumb_job){
        const j=s.thumb_job;
        if(j.running){
          $("#thumbmsg").textContent=`${j.done}/${j.total}…`;
        } else {
          clearInterval(poll);
          $("#thumbmsg").textContent=j.total>0?`${j.done}/${j.total} done ✓`:"none new";
          $("#thumbsbtn").disabled=false;
          loadClips(true);
        }
      }
    },800);
  };
  $("#telbtn").onclick=async()=>{
    $("#telbtn").disabled=true; $("#telmsg").textContent="Extracting…";
    await fetch("api/telemetry_all",{method:"POST"});
    const poll=setInterval(async()=>{
      const s=await fetch("api/status").then(r=>r.json()).catch(()=>null);
      if(s&&s.tel_job){
        const j=s.tel_job;
        if(j.running){
          $("#telmsg").textContent=`${j.done}/${j.total}…`;
        } else {
          clearInterval(poll);
          $("#telmsg").textContent=j.total>0?`${j.done}/${j.total} done ✓`:"none new";
          $("#telbtn").disabled=false;
          loadClips(true);
        }
      }
    },800);
  };
  $("#t_hud").onchange=applyHud;
  $("#t_nerd").onchange=()=>{ if(!$("#t_nerd").checked){ $("#nerd").style.display="none"; } else if(!(tele&&tele.frame_count)&&curEvent){ nerdEvent(); $("#nerd").style.display="block"; } };
  $("#t_map").onchange=()=>showMap($("#t_map").checked);
  $("#dlzip").onclick=()=>{ if(!activeId){return;} $("#vmsg").textContent="Creating ZIP…"; const a=document.createElement("a"); a.href="api/zip?id="+encodeURIComponent(activeId); a.click(); setTimeout(()=>$("#vmsg").textContent="",5000); };
  $("#permdec").onclick=async()=>{ if(!activeId){return;} $("#vmsg").textContent="Decrypting & saving…"; const r=await fetch("api/prepare",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:activeId})}).then(r=>r.json()).catch(()=>({ok:false})); $("#vmsg").textContent=(r&&(r.ok||r.clip))?"✓ permanently saved (folder decrypted/)":"Error"; refreshStatus(); loadClips(true); };
  $("#search").oninput=buildSidebar;
  $("#filterDrive").onchange=buildSidebar;
  $("#filterEvent").onchange=buildSidebar;
  $("#filterHonk").onchange=buildSidebar;
  $("#mapbtn").onclick=()=>{
    const c=$("#mapFilterContainer");
    const show=c.style.display==="none"||c.style.display==="";
    c.style.display=show?"flex":"none";
    if(show){ initMapFilter().then(()=>{ if(_gpsMap) setTimeout(()=>_gpsMap.invalidateSize(),50); }); }
  };
  // Clips must always load even if the GPS map fails (e.g. leaflet-draw CDN blocked in ingress)
  try { await initMapFilter(); } catch(e){ console.error("GPS map init failed:", e); }
  try { await refreshStatus(); } catch(e){ console.error("status failed:", e); }
  await loadClips(false);
  setInterval(()=>refreshStatus().catch(()=>{}),5000);
}
boot();
