"""FastAPI UI. Zero CDN dependencies (works fully offline). Run via `bitswarm serve`."""
from __future__ import annotations
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from .attack import run_all


class AskReq(BaseModel):
    q: str
    mode: str = "hybrid"


class PeerReq(BaseModel):
    addr: str


class TrustReq(BaseModel):
    pubkey: str
    name: str


def create_app(node) -> FastAPI:
    app = FastAPI(title="BitSwarm-RAG")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return PAGE

    @app.get("/api/whoami")
    def whoami():
        return {"name": node.identity.name, "node_id": node.identity.node_id, "pubkey": node.identity.pubkey,
                "api_addr": node.addr(), "embedder": node.embedder.name, "llm": node.backend.name}

    @app.post("/api/ask")
    def ask(r: AskReq):
        if r.mode not in ("hybrid", "dense", "bm25"):
            raise HTTPException(400, "bad mode")
        res = node.ask(r.q, r.mode)
        res["hits"] = [{k: h[k] for k in ("doc_title", "section", "shard_title", "publisher_name", "text", "suspicious", "dense_score")}
                       for h in res["hits"]]
        return res

    @app.get("/api/swarm")
    def swarm():
        return node.swarm_view()

    @app.post("/api/accept/{sid}")
    def accept(sid: str):
        return {"ok": node.accept(sid)}

    @app.post("/api/reject/{sid}")
    def reject(sid: str):
        node.reject(sid); return {"ok": True}

    @app.post("/api/peer")
    def add_peer(r: PeerReq):
        node.peers.add_manual(r.addr); node.sync_once(); return {"ok": True}

    @app.get("/api/trust")
    def trust():
        return [{"pubkey": k, "name": v} for k, v in node.trust.all().items()]

    @app.post("/api/trust")
    def add_trust(r: TrustReq):
        try:
            node.trust.add(r.pubkey, r.name)
        except ValueError:
            raise HTTPException(400, "pubkey must be hex")
        node.rejected.clear()           # re-evaluate previously rejected shards
        return {"ok": True}

    @app.get("/api/audit")
    def audit():
        return list(reversed(node.home.read_audit(100)))

    @app.get("/api/events")
    def events():
        return list(reversed(list(node.events)))

    @app.post("/api/attack")
    def attack():
        return [o.__dict__ for o in run_all()]

    return app


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>BitSwarm-RAG</title>
<style>
:root{--bg:#0d1117;--card:#161b22;--b:#30363d;--t:#e6edf3;--m:#8b949e;--ok:#3fb950;--bad:#f85149;--acc:#58a6ff;--warn:#d29922}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t);font:14px/1.5 system-ui,sans-serif}
header{padding:12px 20px;border-bottom:1px solid var(--b);display:flex;gap:16px;align-items:center;flex-wrap:wrap}
header b{font-size:16px}header span{color:var(--m);font-size:12px}
nav button{background:none;border:0;color:var(--m);padding:8px 14px;cursor:pointer;font-size:14px;border-bottom:2px solid transparent}
nav button.on{color:var(--t);border-color:var(--acc)}main{padding:20px;max-width:1100px;margin:auto}
.card{background:var(--card);border:1px solid var(--b);border-radius:8px;padding:14px;margin-bottom:12px}
input,select,button.pri{background:#0d1117;border:1px solid var(--b);color:var(--t);padding:8px 10px;border-radius:6px;font-size:14px}
button.pri{background:#238636;border-color:#238636;cursor:pointer}button.bad{background:#da3633;border-color:#da3633}
.row{display:flex;gap:8px;margin-bottom:10px}.row input{flex:1}.m{color:var(--m)}.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:12px;border:1px solid var(--b);margin-right:6px}
.chunk{border-left:3px solid var(--acc);padding:6px 10px;margin:6px 0;background:#0d1117;white-space:pre-wrap;font-size:13px}
.chunk.sus{border-color:var(--warn)}pre{white-space:pre-wrap;word-break:break-all;margin:0}
.bar{height:8px;background:#21262d;border-radius:4px;overflow:hidden}.bar i{display:block;height:100%;background:var(--acc)}
.step{display:inline-block;margin-right:10px}table{width:100%;border-collapse:collapse}td,th{padding:6px;border-bottom:1px solid var(--b);text-align:left;vertical-align:top}
canvas{width:100%;height:280px;background:#0d1117;border:1px solid var(--b);border-radius:8px}
.hide{display:none}.flash{animation:fl 1s}@keyframes fl{from{background:#5a1d1d}to{background:transparent}}
</style></head><body>
<header><b>🐝 BitSwarm-RAG</b><span id="who"></span><nav id="nav"></nav></header>
<main>
<section id="t-ask"><div class="row"><input id="q" placeholder="Ask the team's shared knowledge…" onkeydown="if(event.key=='Enter')ask()">
<select id="mode"><option>hybrid</option><option>dense</option><option>bm25</option></select><button class="pri" onclick="ask()">Ask</button></div><div id="ans"></div></section>
<section id="t-swarm" class="hide"><canvas id="cv" width="1000" height="280"></canvas><div id="disc" class="m"></div>
<h3>Offered shards</h3><div id="pend"></div><h3>Transfers &amp; verification</h3><div id="xfer"></div><h3>Mounted shards</h3><div id="shards"></div>
<h3>Rejected</h3><div id="rej"></div>
<div class="row" style="margin-top:12px"><input id="peer" placeholder="Add peer manually  ip:port  (e.g. 192.168.1.20:49501)"><button class="pri" onclick="addPeer()">Add peer</button></div></section>
<section id="t-trust" class="hide"><div class="card"><div class="m">Your public key (give this to teammates):</div><pre id="mykey"></pre></div>
<div class="row"><input id="tk" placeholder="publisher public key (hex)"><input id="tn" placeholder="name" style="max-width:160px"><button class="pri" onclick="addTrust()">Trust</button></div><div id="trust"></div></section>
<section id="t-audit" class="hide"><div id="audit"></div></section>
<section id="t-attack" class="hide"><div class="card">Launches hostile shards against a throwaway node and shows which gate step rejects each.
<br><br><button class="pri bad" onclick="attack()">⚔ Run attack suite</button></div><div id="atk"></div></section>
</main>
<script>
const $=id=>document.getElementById(id);const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const tabs=['ask','swarm','trust','audit','attack'];let cur='ask';
$('nav').innerHTML=tabs.map(t=>`<button id="b-${t}" onclick="show('${t}')">${t[0].toUpperCase()+t.slice(1)}</button>`).join('');
function show(t){cur=t;tabs.forEach(x=>{$('t-'+x).classList.toggle('hide',x!=t);$('b-'+x).classList.toggle('on',x==t)});refresh()}
async function j(u,o){const r=await fetch(u,o);return r.json()}
const post=(u,b)=>j(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});
async function ask(){const q=$('q').value.trim();if(!q)return;$('ans').innerHTML='<div class="m">thinking…</div>';
 const r=await post('/api/ask',{q,mode:$('mode').value});let h='';
 if(r.refused)h+=`<div class="card warn">${esc(r.answer)} <span class="m">(best cosine ${r.best_dense==null?'n/a':r.best_dense.toFixed(2)})</span></div>`;
 else{const p=r.provenance;h+=`<div class="card"><div>${esc(r.answer)}</div><div style="margin-top:10px"><span class="badge ok">✓ ${p.chunks} chunks · ${p.shards} shard(s)</span><span class="badge">publishers: ${esc(p.publishers.join(', '))}</span></div>`;
  r.warnings.forEach(w=>h+=`<div class="warn">⚠ ${esc(w)}</div>`);h+='</div><h4>Citations</h4>';
  r.citations.forEach(c=>h+=`<div class="chunk ${c.suspicious?'sus':''}"><b>[${c.tag}]</b> ${esc(c.doc_title)} › ${esc(c.section)} <span class="m">· shard “${esc(c.shard_title)}” · signed by ${esc(c.publisher)}</span>${c.suspicious?' <span class="warn">⚠ instruction-like text, treated as data</span>':''}\n${esc(c.text.slice(0,500))}</div>`)}
 h+='<details><summary class="m">Raw retrieved chunks</summary>'+r.hits.map(x=>`<div class="chunk ${x.suspicious?'sus':''}"><b>${esc(x.doc_title)}</b> <span class="m">cos=${x.dense_score==null?'-':x.dense_score.toFixed(2)}</span>\n${esc(x.text.slice(0,300))}</div>`).join('')+'</details>';$('ans').innerHTML=h}
let swarm={nodes:[],edges:[],transfers:{}};
async function loadSwarm(){swarm=await j('/api/swarm');
 $('disc').textContent=swarm.discovery.ok?'multicast discovery: on':'multicast discovery: OFF — '+swarm.discovery.error;
 $('pend').innerHTML=Object.entries(swarm.pending).map(([id,p])=>`<div class="card"><b>${esc(p.title)}</b> <span class="m">by ${esc(p.publisher)} · ${p.peers.length} peer(s) have it · ${id.slice(0,12)}</span><br><button class="pri" onclick="acc('${id}')">Accept</button> <button class="pri bad" onclick="rej('${id}')">Reject</button></div>`).join('')||'<span class="m">none</span>';
 $('xfer').innerHTML=Object.entries(swarm.transfers).map(([id,t])=>{const pc=Math.round(100*t.done/Math.max(t.total,1));
  const st=(t.steps||[]).map(s=>`<span class="step ${s.ok?'ok':'bad'}">${s.ok?'✓':'✗'} ${s.step}.${esc(s.name)}</span>`).join('');
  const pp=Object.entries(t.per_peer||{}).map(([k,v])=>`${esc(k)}: ${(v/1024).toFixed(0)} KiB`).join(' · ');
  return `<div class="card"><b>${id.slice(0,12)}</b> <span class="badge">${t.state}</span><div class="bar"><i style="width:${pc}%"></i></div><div class="m">${pp}</div><div>${st}</div>${t.reason?`<div class="bad">${esc(t.reason)}</div>`:''}</div>`}).join('')||'<span class="m">none</span>';
 $('shards').innerHTML='<table><tr><th>title</th><th>publisher</th><th>chunks</th><th>shard</th></tr>'+swarm.shards.map(s=>`<tr><td>${esc(s.title)}</td><td>${esc(s.publisher_name)}</td><td>${s.chunks}</td><td class="m">${s.shard_id.slice(0,12)}</td></tr>`).join('')+'</table>';
 $('rej').innerHTML=Object.entries(swarm.rejected).map(([id,r])=>`<div class="bad">✗ ${id.slice(0,12)} — ${esc(r)}</div>`).join('')||'<span class="m">none</span>'}
const acc=async id=>{await post('/api/accept/'+id);loadSwarm()};const rej=async id=>{await post('/api/reject/'+id);loadSwarm()};
async function addPeer(){await post('/api/peer',{addr:$('peer').value.trim()});loadSwarm()}
async function loadTrust(){const w=await j('/api/whoami');$('mykey').textContent=w.pubkey+'\n(node '+w.node_id+')';
 $('trust').innerHTML='<table>'+(await j('/api/trust')).map(t=>`<tr><td>${esc(t.name)}</td><td class="m">${t.pubkey.slice(0,24)}…</td></tr>`).join('')+'</table>'}
async function addTrust(){await post('/api/trust',{pubkey:$('tk').value.trim(),name:$('tn').value.trim()||'peer'});loadTrust()}
async function loadAudit(){$('audit').innerHTML='<table>'+(await j('/api/audit')).map(a=>`<tr><td class="m">${new Date(a.ts*1000).toLocaleTimeString()}</td><td class="${a.event.includes('ok')?'ok':a.event.includes('reject')?'bad':''}">${esc(a.event)}</td><td class="m">${esc(JSON.stringify({...a,ts:undefined,event:undefined}))}</td></tr>`).join('')+'</table>'}
async function attack(){$('atk').innerHTML='<div class="m">running…</div>';const r=await j('/api/attack',{method:'POST'});
 $('atk').innerHTML='<table><tr><th></th><th>attack</th><th>expected</th><th>got</th><th>reason</th></tr>'+r.map(a=>`<tr class="flash"><td class="${a.passed?'ok':'bad'}">${a.passed?'✓':'✗'}</td><td>${esc(a.name)}</td><td>${esc(a.expected)}</td><td>${esc(a.got)}</td><td class="m">${esc(a.detail)}</td></tr>`).join('')+'</table><p class="ok">'+r.filter(a=>a.passed).length+'/'+r.length+' behaved correctly</p>'}
// swarm graph
const cv=$('cv'),cx=cv.getContext('2d');let ph=0;
function draw(){ph+=0.03;cx.clearRect(0,0,cv.width,cv.height);const n=swarm.nodes,W=cv.width,H=cv.height;if(!n.length){requestAnimationFrame(draw);return}
 const pos={};n.forEach((x,i)=>{if(x.self)pos[x.id]=[W/2,H/2];else{const k=i-1,m=Math.max(n.length-1,1),a=2*Math.PI*k/m+ph*0.05;pos[x.id]=[W/2+Math.cos(a)*300,H/2+Math.sin(a)*100]}});
 const busy=Object.values(swarm.transfers||{}).some(t=>t.state=='downloading'||t.state=='verifying');
 (swarm.edges||[]).forEach(e=>{const a=pos[e.a],b=pos[e.b];if(!a||!b)return;cx.strokeStyle=busy?'#58a6ff':'#30363d';cx.lineWidth=busy?2:1;cx.beginPath();cx.moveTo(...a);cx.lineTo(...b);cx.stroke();
  if(busy){const t=(ph%1);cx.fillStyle='#58a6ff';cx.beginPath();cx.arc(b[0]+(a[0]-b[0])*t,b[1]+(a[1]-b[1])*t,4,0,7);cx.fill()}});
 n.forEach(x=>{const p=pos[x.id];cx.fillStyle=x.self?'#238636':(x.manual?'#d29922':'#1f6feb');cx.beginPath();cx.arc(p[0],p[1],14,0,7);cx.fill();cx.fillStyle='#e6edf3';cx.font='12px sans-serif';cx.textAlign='center';cx.fillText(x.name,p[0],p[1]+30)});
 requestAnimationFrame(draw)}
draw();
function refresh(){if(cur=='swarm')loadSwarm();if(cur=='trust')loadTrust();if(cur=='audit')loadAudit()}
setInterval(()=>{if(cur=='swarm')loadSwarm()},1500);
j('/api/whoami').then(w=>$('who').textContent=`${w.name} · ${w.node_id} · embed ${w.embedder} · llm ${w.llm}`);show('ask');
</script></body></html>"""
