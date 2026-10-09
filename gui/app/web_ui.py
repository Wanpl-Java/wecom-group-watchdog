"""Browser-based console — cyber HUD UI, no Tk/Tcl required."""
from __future__ import annotations

import threading
import webbrowser
from typing import Any, Dict

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from client import WatchdogClient

app = FastAPI(title="WeCom Watchdog Console")
DEFAULT_BASE = "http://127.0.0.1:8092"


class IntervalBody(BaseModel):
    base_url: str = DEFAULT_BASE
    minutes: int = Field(5, ge=0, le=1440)


class SuggestBody(BaseModel):
    base_url: str = DEFAULT_BASE
    question: str
    group_name: str = "【JS】GUI模拟群"


class ScanBody(BaseModel):
    base_url: str = DEFAULT_BASE


# Style refs: glassmorphism + cyan neon HUD
# inspired by open-source cyber dashboards (omnimcp-console / cyberpunk-ui tokens)
INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Watchdog Console // TECH HUD</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
  <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700&family=JetBrains+Mono:wght@400;600&family=Noto+Sans+SC:wght@400;500;700&display=swap" rel="stylesheet"/>
  <style>
    :root {
      --void: #05060f;
      --panel: rgba(12, 18, 36, 0.62);
      --panel-strong: rgba(16, 24, 48, 0.82);
      --cyan: #22d3ee;
      --cyan-dim: #0891b2;
      --mint: #34d399;
      --warn: #fbbf24;
      --danger: #fb7185;
      --text: #e8eefc;
      --muted: #8b9bb8;
      --line: rgba(34, 211, 238, 0.22);
      --glow: 0 0 24px rgba(34, 211, 238, 0.35);
      --radius: 16px;
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      color: var(--text);
      font-family: "Noto Sans SC", "Segoe UI", sans-serif;
      background: var(--void);
      overflow-x: hidden;
    }
    .bg {
      position: fixed; inset: 0; z-index: 0; pointer-events: none;
      background:
        radial-gradient(900px 500px at 12% -10%, rgba(34,211,238,.18), transparent 55%),
        radial-gradient(700px 420px at 90% 10%, rgba(52,211,153,.12), transparent 50%),
        radial-gradient(600px 400px at 70% 90%, rgba(56,189,248,.10), transparent 55%),
        linear-gradient(180deg, #05060f 0%, #070b18 100%);
    }
    .bg::before {
      content: "";
      position: absolute; inset: 0;
      background-image:
        linear-gradient(rgba(34,211,238,.05) 1px, transparent 1px),
        linear-gradient(90deg, rgba(34,211,238,.05) 1px, transparent 1px);
      background-size: 48px 48px;
      mask-image: radial-gradient(ellipse at center, #000 35%, transparent 80%);
      animation: gridDrift 28s linear infinite;
      opacity: .55;
    }
    .bg::after {
      content: "";
      position: absolute; inset: 0;
      background: repeating-linear-gradient(
        0deg,
        transparent,
        transparent 2px,
        rgba(0,0,0,.12) 3px
      );
      opacity: .25;
      pointer-events: none;
    }
    @keyframes gridDrift {
      from { background-position: 0 0, 0 0; }
      to { background-position: 48px 48px, 48px 48px; }
    }
    @keyframes pulse {
      0%, 100% { box-shadow: 0 0 0 0 rgba(52,211,153,.55); }
      50% { box-shadow: 0 0 0 8px rgba(52,211,153,0); }
    }
    @keyframes shimmer {
      0% { background-position: 0% 50%; }
      100% { background-position: 200% 50%; }
    }
    .wrap {
      position: relative; z-index: 1;
      max-width: 1080px; margin: 0 auto;
      padding: 28px 18px 56px;
    }
    .hero {
      display: flex; flex-wrap: wrap; gap: 16px;
      align-items: flex-end; justify-content: space-between;
      margin-bottom: 22px;
    }
    .brand-kicker {
      font-family: Orbitron, sans-serif;
      font-size: .72rem; letter-spacing: .28em;
      color: var(--cyan); text-transform: uppercase;
      text-shadow: 0 0 12px rgba(34,211,238,.55);
      margin-bottom: 8px;
    }
    h1 {
      margin: 0;
      font-family: Orbitron, sans-serif;
      font-size: clamp(1.45rem, 3vw, 2rem);
      letter-spacing: .06em;
      background: linear-gradient(90deg, #fff, #67e8f9 45%, #34d399 90%);
      -webkit-background-clip: text; background-clip: text;
      color: transparent;
    }
    .hero-sub { color: var(--muted); margin-top: 8px; font-size: .95rem; max-width: 36rem; }
    .badge {
      display: inline-flex; align-items: center; gap: 8px;
      padding: 8px 14px; border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(8,20,36,.7);
      backdrop-filter: blur(12px);
      font-family: "JetBrains Mono", monospace;
      font-size: .78rem;
    }
    .dot {
      width: 9px; height: 9px; border-radius: 50%;
      background: var(--warn);
    }
    .dot.on { background: var(--mint); animation: pulse 1.8s infinite; }
    .dot.off { background: var(--danger); }

    .grid {
      display: grid;
      grid-template-columns: 1.05fr .95fr;
      gap: 16px;
    }
    @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }

    .card {
      position: relative;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 18px;
      backdrop-filter: blur(22px);
      box-shadow: inset 0 1px 0 rgba(255,255,255,.06), 0 18px 40px rgba(0,0,0,.35);
      overflow: hidden;
    }
    .card::before {
      content: "";
      position: absolute; left: 0; right: 0; top: 0; height: 1px;
      background: linear-gradient(90deg, transparent, rgba(34,211,238,.7), transparent);
    }
    .card.hud::after {
      content: "";
      position: absolute; inset: 10px;
      border: 1px solid rgba(34,211,238,.08);
      border-radius: 12px;
      pointer-events: none;
      mask-image:
        linear-gradient(#000 0 0) content-box,
        linear-gradient(#000 0 0);
      -webkit-mask-composite: xor; mask-composite: exclude;
      padding: 0;
      background:
        linear-gradient(var(--cyan), var(--cyan)) left top / 18px 2px no-repeat,
        linear-gradient(var(--cyan), var(--cyan)) left top / 2px 18px no-repeat,
        linear-gradient(var(--cyan), var(--cyan)) right top / 18px 2px no-repeat,
        linear-gradient(var(--cyan), var(--cyan)) right top / 2px 18px no-repeat,
        linear-gradient(var(--cyan), var(--cyan)) left bottom / 18px 2px no-repeat,
        linear-gradient(var(--cyan), var(--cyan)) left bottom / 2px 18px no-repeat,
        linear-gradient(var(--cyan), var(--cyan)) right bottom / 18px 2px no-repeat,
        linear-gradient(var(--cyan), var(--cyan)) right bottom / 2px 18px no-repeat;
      opacity: .85;
    }
    .card h2 {
      margin: 0 0 12px;
      font-family: Orbitron, sans-serif;
      font-size: .86rem;
      letter-spacing: .16em;
      color: #a5f3fc;
      text-transform: uppercase;
    }
    label {
      display: block;
      font-size: .78rem;
      color: var(--muted);
      margin: 10px 0 6px;
      letter-spacing: .04em;
    }
    input, select, textarea {
      width: 100%;
      padding: 11px 12px;
      border-radius: 12px;
      border: 1px solid rgba(34,211,238,.22);
      background: rgba(3, 8, 20, .72);
      color: var(--text);
      font: inherit;
      outline: none;
      transition: border-color .2s, box-shadow .2s;
    }
    input:focus, select:focus, textarea:focus {
      border-color: rgba(34,211,238,.7);
      box-shadow: 0 0 0 3px rgba(34,211,238,.15), var(--glow);
    }
    textarea {
      min-height: 120px;
      resize: vertical;
      font-family: "JetBrains Mono", "Noto Sans SC", monospace;
      font-size: .88rem;
      line-height: 1.5;
    }
    .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: end; }
    .row > .grow { flex: 1; min-width: 160px; }
    .btns { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }
    button {
      appearance: none; border: 1px solid transparent;
      border-radius: 12px; padding: 11px 16px;
      font-family: Orbitron, "Noto Sans SC", sans-serif;
      font-size: .78rem; letter-spacing: .08em;
      cursor: pointer; color: #041018;
      background: linear-gradient(120deg, #67e8f9, #22d3ee 40%, #34d399);
      background-size: 200% 200%;
      box-shadow: var(--glow);
      transition: transform .15s, filter .15s, background-position .4s;
    }
    button:hover { transform: translateY(-1px); background-position: 100% 50%; filter: brightness(1.05); }
    button:active { transform: translateY(0); }
    button.ghost {
      color: var(--cyan);
      background: rgba(34,211,238,.08);
      border-color: rgba(34,211,238,.35);
      box-shadow: none;
    }
    button.ghost:hover { background: rgba(34,211,238,.16); }
    button:disabled { opacity: .55; cursor: wait; transform: none; }

    #status {
      margin-top: 12px;
      padding: 10px 12px;
      border-radius: 12px;
      background: rgba(2, 10, 24, .55);
      border: 1px dashed rgba(34,211,238,.25);
      color: var(--muted);
      font-family: "JetBrains Mono", monospace;
      font-size: .8rem;
      white-space: pre-wrap;
      min-height: 42px;
    }
    .span-2 { grid-column: 1 / -1; }
    #out {
      margin: 0;
      min-height: 260px;
      max-height: 480px;
      overflow: auto;
      white-space: pre-wrap;
      font-family: "JetBrains Mono", Consolas, monospace;
      font-size: .86rem;
      line-height: 1.55;
      color: #d1fae5;
      background:
        linear-gradient(180deg, rgba(6,20,30,.9), rgba(3,10,20,.95));
      border: 1px solid rgba(52,211,153,.25);
      border-radius: 12px;
      padding: 14px 14px 14px 16px;
      box-shadow: inset 0 0 40px rgba(52,211,153,.05);
    }
    .chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
    .chip {
      border: 1px solid rgba(34,211,238,.28);
      background: rgba(34,211,238,.08);
      color: #a5f3fc;
      border-radius: 999px;
      padding: 6px 12px;
      font-size: .78rem;
      cursor: pointer;
    }
    .chip:hover { border-color: var(--cyan); box-shadow: var(--glow); }
    .footer {
      margin-top: 18px; color: var(--muted);
      font-size: .75rem; letter-spacing: .06em;
      font-family: Orbitron, sans-serif;
      text-align: center; opacity: .8;
    }
    @media (prefers-reduced-motion: reduce) {
      .bg::before, .dot.on { animation: none !important; }
    }
  </style>
</head>
<body>
  <div class="bg" aria-hidden="true"></div>
  <div class="wrap">
    <header class="hero">
      <div>
        <div class="brand-kicker">Fit2Cloud // JumpServer Ops</div>
        <h1>WATCHDOG CONSOLE</h1>
        <p class="hero-sub">科技感控制台：定时校验 · 模拟话术 · 直连本地 Watchdog API</p>
      </div>
      <div class="badge" id="liveBadge">
        <span class="dot" id="liveDot"></span>
        <span id="liveText">STANDBY</span>
      </div>
    </header>

    <div class="grid">
      <section class="card hud">
        <h2>01 // Link</h2>
        <label>Watchdog Base URL</label>
        <div class="row">
          <div class="grow"><input id="base" value="http://127.0.0.1:8092"/></div>
          <button class="ghost" type="button" onclick="health()">PING</button>
        </div>
        <div id="status">状态：未检测</div>
      </section>

      <section class="card hud">
        <h2>02 // Interval</h2>
        <div class="row">
          <div class="grow">
            <label>预设间隔</label>
            <select id="preset" onchange="syncCustom()">
              <option value="5">5 分钟</option>
              <option value="10">10 分钟</option>
              <option value="15">15 分钟</option>
              <option value="30">30 分钟</option>
              <option value="custom">自定义</option>
            </select>
          </div>
          <div class="grow">
            <label>自定义 (min)</label>
            <input id="customMin" type="number" min="0" max="1440" value="5"/>
          </div>
        </div>
        <div class="btns">
          <button type="button" onclick="setIntervalMin()">应用到服务端</button>
          <button class="ghost" type="button" onclick="scan()">立即扫描</button>
          <button type="button" onclick="sendDigest()">发送待跟进表</button>
        </div>
      </section>

      <section class="card hud span-2">
        <h2>03 // Simulate Q&amp;A</h2>
        <label>快捷样例</label>
        <div class="chips" id="chips"></div>
        <label>模拟群名</label>
        <input id="group" value="【JS】GUI模拟群"/>
        <label>客户问题</label>
        <textarea id="q">JumpServer远程应用发布机怎么部署</textarea>
        <div class="btns">
          <button type="button" id="btnSuggest" onclick="suggest()">发送模拟问答</button>
          <button class="ghost" type="button" onclick="clearOut()">清空输出</button>
        </div>
      </section>

      <section class="card span-2">
        <h2>04 // Telemetry Output</h2>
        <pre id="out">等待指令…</pre>
      </section>
    </div>
    <p class="footer">WECOM · WATCHDOG · LOCAL HUD · CYAN NEON GLASS</p>
  </div>

<script>
const SAMPLES = [
  "JumpServer远程应用发布机怎么部署",
  "登录提示配置文件有问题无法登录 DOMAINS",
  "Tinker 离线怎么办",
  "资产连接超时怎么排查"
];
const chips = document.getElementById('chips');
SAMPLES.forEach(s => {
  const el = document.createElement('button');
  el.type = 'button';
  el.className = 'chip';
  el.textContent = s.length > 18 ? s.slice(0,18) + '…' : s;
  el.title = s;
  el.onclick = () => { document.getElementById('q').value = s; };
  chips.appendChild(el);
});

function base(){ return document.getElementById('base').value.trim() || 'http://127.0.0.1:8092'; }
function minutes(){
  const p = document.getElementById('preset').value;
  if(p !== 'custom') return parseInt(p,10);
  return parseInt(document.getElementById('customMin').value||'5',10);
}
function syncCustom(){
  if(document.getElementById('preset').value !== 'custom'){
    document.getElementById('customMin').value = document.getElementById('preset').value;
  }
}
function setLive(on, text){
  const dot = document.getElementById('liveDot');
  const live = document.getElementById('liveText');
  dot.className = 'dot ' + (on ? 'on' : 'off');
  live.textContent = text;
}
function setStatus(msg){ document.getElementById('status').textContent = msg; }

async function health(){
  setStatus('PING …');
  try {
    const r = await fetch('/api/health', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({base_url: base()})});
    const d = await r.json();
    if(d.ok){
      setLive(true, 'ONLINE');
      setStatus(`ONLINE · mode=${d.workbuddy_mode} · scan=${d.scan_interval_minutes}min · source=${d.message_source}`);
    } else {
      setLive(false, 'OFFLINE');
      setStatus(`OFFLINE — ${d.error||r.status}`);
    }
  } catch(e) {
    setLive(false, 'OFFLINE');
    setStatus(`OFFLINE — ${e}`);
  }
}
async function setIntervalMin(){
  const r = await fetch('/api/interval', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({base_url: base(), minutes: minutes()})});
  const d = await r.json();
  setStatus(d.ok ? `INTERVAL applied: ${d.scan_interval_minutes} min` : `失败: ${d.error||r.status}`);
  if(d.ok) health();
}
async function scan(){
  setStatus('FORCE SCAN …');
  const r = await fetch('/api/scan', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({base_url: base()})});
  const d = await r.json();
  document.getElementById('out').textContent = JSON.stringify(d, null, 2);
  setStatus(d.error ? `scan 失败: ${d.error}` : 'scan 完成');
}
async function sendDigest(){
  if(!confirm('将先同步群消息并重新扫描，再把未回复客户表发到企微。确认？')) return;
  setStatus('同步并发送待跟进表…');
  const r = await fetch('/api/pending-digest', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({base_url: base()})});
  const d = await r.json();
  document.getElementById('out').textContent = JSON.stringify(d, null, 2);
  setStatus(d.error ? `发送失败: ${d.error}` : `已发送 ${d.count||0} 条`);
}
async function suggest(){
  const q = document.getElementById('q').value.trim();
  if(!q){ alert('请先填写模拟问题'); return; }
  const btn = document.getElementById('btnSuggest');
  btn.disabled = true;
  setStatus('生成建议中（可能需 10–60 秒）…');
  try {
    const r = await fetch('/api/suggest', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({base_url: base(), question:q, group_name: document.getElementById('group').value})});
    const d = await r.json();
    if(d.error){
      setStatus(`suggest 失败: ${d.error}`);
      document.getElementById('out').textContent = d.error;
      return;
    }
    document.getElementById('out').textContent =
`=== 模拟问答 ===
群: ${d.group_name}
问: ${d.question}
来源: ${d.source}

${d.suggestion}`;
    setStatus(`suggest ok · source=${d.source}`);
  } finally {
    btn.disabled = false;
  }
}
function clearOut(){ document.getElementById('out').textContent = '等待指令…'; }
syncCustom();
health();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


@app.post("/api/health")
def api_health(body: ScanBody) -> Dict[str, Any]:
    try:
        return WatchdogClient(body.base_url).healthz()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@app.post("/api/interval")
def api_interval(body: IntervalBody) -> Dict[str, Any]:
    try:
        return WatchdogClient(body.base_url).set_interval(body.minutes)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@app.post("/api/scan")
def api_scan(body: ScanBody) -> Dict[str, Any]:
    try:
        return WatchdogClient(body.base_url).force_scan()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@app.post("/api/pending-digest")
def api_pending_digest(body: ScanBody) -> Dict[str, Any]:
    try:
        return WatchdogClient(body.base_url).push_pending_digest()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@app.post("/api/suggest")
def api_suggest(body: SuggestBody) -> Dict[str, Any]:
    if not body.question.strip():
        raise HTTPException(400, "question required")
    try:
        return WatchdogClient(body.base_url).suggest(body.question, group_name=body.group_name)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def main() -> None:
    def _open() -> None:
        webbrowser.open("http://127.0.0.1:8765/")

    threading.Timer(1.0, _open).start()
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
