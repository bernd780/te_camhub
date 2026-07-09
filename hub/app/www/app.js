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
  bar.append(bulkbtn,bulkmsg);m.append(bar);
  const grid=el("div","clipgrid");m.append(grid);
  let clips;try{clips=await jget("api/clips");}catch(e){return;}
  const enc=clips.filter(c=>c.encrypted).length;
  info.textContent=`${clips.length} Clips · ${enc} verschlüsselt`;
  if(!clips.length){info.textContent="Keine Aufnahmen gefunden.";return;}
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
async function openClip(c){
  toast("Bereite Clip vor…");
  try{await jpost("api/prepare",{id:c.id});}catch(e){}
  const cams=["front","left_repeater","right_repeater","back","left_pillar","right_pillar"];
  const wrap=el("div","player");
  const bar=el("div","bar",`<b>${c.timestamp.replace("_"," ")}</b>`);
  const x=el("button","x","✕");x.onclick=()=>wrap.remove();bar.append(x);
  wrap.append(bar);
  const grid=el("div","grid");
  let any=false;
  cams.forEach(cam=>{
    const v=document.createElement("video");v.controls=true;v.playsInline=true;v.muted=true;
    v.src="media/EncryptedClips/"+c.folder.replace(/^EncryptedClips\/?/,"")+"/"+c.timestamp+"-"+cam+".mp4";
    // try both encrypted and plain locations via media resolver:
    v.src="media/"+encodeURI(c.folder+"/"+c.timestamp+"-"+cam+".mp4");
    v.onerror=()=>{v.remove();};
    grid.append(v);any=true;
  });
  wrap.append(grid);document.body.append(wrap);
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
    it.append(el("div","ic",ent.dir?"📁":ent.image?"🖼️":"📄"));
    const nm=el("div","nm",ent.name);it.append(nm);
    it.append(el("div","sz",ent.dir?"":human(ent.size)));
    const act=el("div","act");
    if(!ent.dir){const dl=el("button","iconbtn","⬇️");dl.title="Download";dl.onclick=e=>{e.stopPropagation();location.href="api/files/download?path="+encodeURIComponent(rel);};act.append(dl);}
    const rn=el("button","iconbtn","✏️");rn.title="Umbenennen";rn.onclick=async e=>{e.stopPropagation();const n=prompt("Neuer Name",ent.name);if(n){await jpost("api/files/rename",{path:rel,name:n});viewFiles($("#main"),path);}};
    const del=el("button","iconbtn","🗑️");del.title="Löschen";del.onclick=async e=>{e.stopPropagation();if(confirm("Löschen: "+ent.name+"?")){await jpost("api/files/delete",{path:rel});viewFiles($("#main"),path);}};
    act.append(rn,del);it.append(act);
    it.onclick=()=>{if(ent.dir)viewFiles($("#main"),rel);else if(ent.image)lightbox(rel,ent.name);};
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
  const box=el("div");box.innerHTML=`
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
      ${chk("Alle Inhalte bei WLAN aufs NAS synchronisieren","s_sync_all_content",c.sync_all_content==='true')}
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
  $("#nastest").onclick=async()=>{$("#nasmsg").textContent="Teste…";
    const r=await jget("api/nas/test");$("#nasmsg").textContent=r.ok?("✓ OK"+(r.writable?" (schreibbar)":" (nur lesbar)")):("✗ "+(r.error||"Fehler"));};
  $("#blepair").onclick=async()=>{$("#blemsg").textContent="Koppeln…";const r=await jpost("api/ble/pair",{});$("#blemsg").textContent=r.ok?"✓ ok":"✗ Fehler";};
  $("#savebtn").onclick=async()=>{
    const fields=["archive_server","share_name","share_user","ssid","ap_ssid","tesla_ble_vin",
      "telegram_chat_id","retention_days","retention_free_gb","vault_autolock_min","time_zone","teslausb_hostname"];
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
