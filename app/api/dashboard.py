"""A single self-contained dashboard page.

No build step, no CDN, no external fonts -- one file that polls the JSON API.
Exposed through cloudflared rather than an ingress, so nothing inbound is
opened, and it is read-only because the process it reports on has no write path
to any exchange.
"""

from __future__ import annotations

_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Delta India paper bot</title>
<style>
 :root{--bg:#0f1115;--panel:#171a21;--line:#252a34;--fg:#e6e8ec;--dim:#8b93a3;
       --up:#3fb950;--down:#f85149;--warn:#d29922;--accent:#58a6ff}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);
      font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
 header{padding:12px 16px;border-bottom:1px solid var(--line);
        display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
 h1{font-size:15px;margin:0;font-weight:600}
 .paper{background:var(--warn);color:#000;padding:1px 7px;border-radius:3px;
        font-weight:700;font-size:11px;letter-spacing:.04em}
 .grid{display:grid;gap:12px;padding:12px;
       grid-template-columns:repeat(auto-fit,minmax(330px,1fr))}
 .panel{background:var(--panel);border:1px solid var(--line);border-radius:6px;
        padding:10px 12px;overflow-x:auto}
 .panel h2{font-size:11px;text-transform:uppercase;letter-spacing:.08em;
           color:var(--dim);margin:0 0 8px;font-weight:600}
 table{border-collapse:collapse;width:100%;font-size:12px}
 th,td{text-align:left;padding:3px 8px 3px 0;white-space:nowrap}
 th{color:var(--dim);font-weight:500}
 tr+tr td{border-top:1px solid var(--line)}
 .k{color:var(--dim);padding-right:12px}
 .up{color:var(--up)}.down{color:var(--down)}.warn{color:var(--warn)}
 .dim{color:var(--dim)}
 .dot{display:inline-block;width:8px;height:8px;border-radius:50%;
      margin-right:6px;vertical-align:middle}
 .ok{background:var(--up)}.bad{background:var(--down)}.mid{background:var(--warn)}
 .bar{height:4px;background:var(--line);border-radius:2px;overflow:hidden;
      margin-top:3px;width:120px}
 .bar>i{display:block;height:100%;background:var(--accent)}
 footer{padding:8px 16px;color:var(--dim);font-size:11px;
        border-top:1px solid var(--line)}
</style></head><body>
<header>
  <h1>Delta India bot</h1><span class="paper">PAPER ONLY</span>
  <span id="hdr" class="dim"></span>
</header>
<div class="grid">
  <div class="panel"><h2>System</h2><table id="system"></table></div>
  <div class="panel"><h2>Risk</h2><table id="risk"></table></div>
  <div class="panel" style="grid-column:1/-1"><h2>Market</h2><table id="market"></table></div>
  <div class="panel" style="grid-column:1/-1"><h2>Open positions</h2><table id="positions"></table></div>
  <div class="panel" style="grid-column:1/-1"><h2>Recent evaluations</h2><table id="signals"></table></div>
  <div class="panel" style="grid-column:1/-1"><h2>Trading journal</h2><table id="trades"></table></div>
  <div class="panel" style="grid-column:1/-1"><h2>System events</h2><table id="events"></table></div>
</div>
<footer>
  Timestamps display IST; storage is UTC. This bot cannot place a real order --
  no order-placement method exists in the process.
  <span id="tick" class="dim"></span>
</footer>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const num=(v,d=2)=>v==null?'-':Number(v).toFixed(d);
const sign=v=>v==null?'dim':(v>0?'up':(v<0?'down':'dim'));

function kv(rows){return rows.map(([k,v,c])=>
  `<tr><td class="k">${esc(k)}</td><td class="${c||''}">${v}</td></tr>`).join('');}
function table(cols,rows){
  if(!rows.length)return '<tr><td class="dim">nothing yet</td></tr>';
  return '<tr>'+cols.map(c=>`<th>${esc(c)}</th>`).join('')+'</tr>'+
    rows.map(r=>'<tr>'+r.map(c=>`<td class="${c[1]||''}">${c[0]}</td>`).join('')+'</tr>').join('');
}
const get=async p=>{try{const r=await fetch(p);return await r.json();}catch(e){return null;}};

async function refresh(){
  const [s,m,p,r,sig,tr,ev]=await Promise.all(
    ['/api/status','/api/market','/api/positions','/api/risk','/api/signals?limit=15',
     '/api/trades?limit=15','/api/events?limit=15'].map(get));

  if(s){
    const dot=s.healthy?'ok':'bad', rdot=s.ready?'ok':'mid';
    $('hdr').innerHTML=`<span class="dot ${dot}"></span>${s.healthy?'healthy':'UNHEALTHY'}`+
      ` &nbsp;<span class="dot ${rdot}"></span>${s.ready?'ready':'warming up'}`+
      ` &nbsp;<span class="dim">${esc(s.strategy_version)}</span>`;
    $('system').innerHTML=kv([
      ['websocket', s.ws_connected?'<span class="up">connected</span>':'<span class="down">disconnected</span>'],
      ['data age', num(s.seconds_since_ws_message,1)+'s',
        s.seconds_since_ws_message<30?'up':'down'],
      ['last closed 1m', esc(s.last_closed_1m_ist||'-')],
      ['recent gaps', s.recent_gaps, s.recent_gaps?'down':'dim'],
      ['uptime', (s.uptime_seconds/3600).toFixed(2)+'h'],
      ['reconnects', s.feed?s.feed.websocket_reconnects:0],
      ['stale events', s.feed?s.feed.stale_feed_events:0],
      ['signals / rejected', `${s.metrics.signals_detected} / ${s.metrics.signals_rejected}`],
      ['orders / fills', `${s.metrics.orders} / ${s.metrics.fills}`],
      ['failing checks', s.failing_checks.length?
        `<span class="down">${esc(s.failing_checks.join(', '))}</span>`:'<span class="dim">none</span>'],
      ['config hash', `<span class="dim">${esc(s.strategy_config_hash)}</span>`],
    ]);
  }
  if(r){
    const used=Math.min(100,100*r.daily_loss_pct/2);
    $('risk').innerHTML=kv([
      ['equity','$'+num(r.equity)],
      ['daily P&L','$'+num(r.daily_pnl),sign(r.daily_pnl)],
      ['daily loss remaining','$'+num(r.daily_loss_remaining)+
        `<div class="bar"><i style="width:${used}%"></i></div>`],
      ['drawdown',num(r.drawdown_pct,2)+'%',r.drawdown_pct>5?'warn':'dim'],
      ['trades today',`${r.trades_today} / ${r.max_trades_per_day}`],
      ['consecutive losses',`${r.consecutive_losses} / ${r.max_consecutive_losses}`,
        r.consecutive_losses?'warn':'dim'],
      ['risk per trade',num(r.risk_per_trade_pct,2)+'%'],
      ['minimum RR',num(r.minimum_rr,1)],
      ['cooldown (trade)',r.cooldown_trade_remaining+'s',r.cooldown_trade_remaining?'warn':'dim'],
      ['cooldown (loss)',r.cooldown_loss_remaining+'s',r.cooldown_loss_remaining?'warn':'dim'],
      ['record',`${r.wins}W / ${r.losses}L`],
    ]);
  }
  if(m)$('market').innerHTML=table(
    ['symbol','state','price','trend','ADX','+DI','-DI','%R','bars','gaps','last bar'],
    m.map(x=>[[esc(x.symbol)],[esc(x.state),x.state==='LIVE'?'up':'warn'],
      [num(x.last_price,2)],[esc(x.trend||'-'),x.trend==='up'?'up':'down'],
      [num(x.adx,1),x.adx>=25?'up':'dim'],[num(x.plus_di,1)],[num(x.minus_di,1)],
      [num(x.williams_r,1)],[x.bars_1m,'dim'],[x.gaps,x.gaps?'down':'dim'],
      [esc(x.last_closed_1m_ist||'-'),'dim']]));

  if(p)$('positions').innerHTML=table(
    ['symbol','side','qty','entry','stop','target','price','uPnL','R','opened'],
    p.map(x=>[[esc(x.symbol)],[esc(x.side),x.side==='LONG'?'up':'down'],[x.quantity],
      [num(x.entry,2)],[num(x.stop,2),'down'],[num(x.target,2),'up'],
      [num(x.current_price,2)],['$'+num(x.unrealized_pnl),sign(x.unrealized_pnl)],
      [num(x.r,2),sign(x.r)],[esc(x.opened_ist),'dim']]));

  if(sig)$('signals').innerHTML=table(
    ['bar (IST)','symbol','outcome','dir','entry','stop','target','why'],
    sig.map(x=>[[esc(x.bar_open_ist||'-'),'dim'],[esc(x.symbol)],
      [esc(x.outcome),x.outcome==='APPROVED'?'up':(x.outcome==='REJECTED'?'warn':'dim')],
      [x.direction==null?'-':(x.direction>0?'L':'S')],
      [num(x.entry_price,2)],[num(x.stop_price,2)],[num(x.target_price,2)],
      [esc(x.rejection_reason||(x.conditions_failed||[]).slice(0,2).join(', ')),'dim']]));

  if(tr)$('trades').innerHTML=table(
    ['opened (IST)','symbol','side','entry','exit','qty','PnL','R','reason','closed'],
    tr.map(x=>[[esc(x.opened_ist),'dim'],[esc(x.symbol)],
      [esc(x.side),x.side==='LONG'?'up':'down'],[num(x.entry,2)],[num(x.exit,2)],
      [x.quantity],['$'+num(x.pnl),sign(x.pnl)],[num(x.r,2),sign(x.r)],
      [esc(x.reason||'-')],[esc(x.closed_ist||'open'),'dim']]));

  if(ev)$('events').innerHTML=table(['component','event','severity','symbol'],
    ev.map(x=>[[esc(x.component)],[esc(x.event_type)],
      [esc(x.severity),x.severity==='INFO'?'dim':(x.severity==='WARNING'?'warn':'down')],
      [esc(x.symbol||'-'),'dim']]));

  $('tick').textContent=' | refreshed '+new Date().toLocaleTimeString();
}
refresh(); setInterval(refresh, 5000);
</script></body></html>
"""


def render_dashboard() -> str:
    return _HTML
