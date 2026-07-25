"""
dashboard/server.py
=====================
داشبورد وب سبک. فقط از فایل‌های داخل data/ می‌خواند (که bot.py می‌نویسد) --
هیچ ارتباط مستقیمی با API صرافی ندارد، پس حتی اگر داشبورد down شود بات
مستقل به کارش ادامه می‌دهد.
اجرا: uvicorn dashboard.server:app --host 0.0.0.0 --port 8000
"""
import os
import json
import datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
TRADE_LOG = os.path.join(DATA_DIR, "trade_log.jsonl")
HEARTBEAT_FILE = os.path.join(DATA_DIR, "heartbeat.json")
RISK_STATE_FILE = os.path.join(DATA_DIR, "risk_state.json")

app = FastAPI(title="CoinEx Bot Dashboard")


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


@app.get("/api/status")
def api_status():
    heartbeat = read_json(HEARTBEAT_FILE, {})
    risk_state = read_json(RISK_STATE_FILE, {})
    events = read_jsonl(TRADE_LOG)
    exits = [e for e in events if e.get("type") == "exit"]

    equity_curve = [100.0]
    for e in exits:
        equity_curve.append(equity_curve[-1] * (1 + e.get("equity_change_pct", 0) / 100))

    stale = True
    if heartbeat.get("ts"):
        last_ts = datetime.datetime.fromisoformat(heartbeat["ts"])
        stale = (datetime.datetime.utcnow() - last_ts).total_seconds() > 15 * 60

    wins = [e for e in exits if e.get("equity_change_pct", 0) > 0]
    return JSONResponse({
        "heartbeat": heartbeat,
        "risk_state": risk_state,
        "is_stale": stale,
        "total_trades": len(exits),
        "win_rate_pct": round(100 * len(wins) / len(exits), 1) if exits else None,
        "equity_multiple": round(equity_curve[-1] / 100, 4),
        "equity_curve": equity_curve,
        "recent_events": events[-30:][::-1],
    })


@app.get("/", response_class=HTMLResponse)
def dashboard_page():
    return HTML_PAGE


HTML_PAGE = """
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<title>داشبورد ربات معاملاتی BTCUSDT</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  :root{--bg:#0b0f14;--card:#131a22;--line:#22303c;--green:#22c55e;--red:#ef4444;--muted:#8aa0b2;--text:#e8eef3;}
  *{box-sizing:border-box;}
  body{background:var(--bg);color:var(--text);font-family:-apple-system,Segoe UI,Tahoma,sans-serif;margin:0;padding:24px;}
  h1{font-size:20px;margin:0 0 20px;}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:20px;}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;}
  .label{color:var(--muted);font-size:12px;margin-bottom:6px;}
  .value{font-size:24px;font-weight:600;}
  .badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:600;}
  .badge-paper{background:#1e3a5f;color:#7dd3fc;}
  .badge-live{background:#3a1e1e;color:#fca5a5;}
  .badge-stale{background:#5f1e1e;color:#fca5a5;}
  .green{color:var(--green);} .red{color:var(--red);}
  table{width:100%;border-collapse:collapse;font-size:13px;}
  th,td{padding:8px;text-align:right;border-bottom:1px solid var(--line);}
  th{color:var(--muted);font-weight:500;}
  canvas{max-height:320px;}
</style>
</head>
<body>
<h1>📊 داشبورد ربات BTCUSDT — شکست فشرده + Whale + پلکانی</h1>
<div id="statusBar" class="grid"></div>
<div class="card" style="margin-bottom:20px;"><canvas id="equityChart"></canvas></div>
<div class="card">
  <div class="label">آخرین رویدادها</div>
  <table id="eventsTable"><thead><tr><th>زمان</th><th>نوع</th><th>حالت</th><th>جهت</th><th>قیمت</th><th>نتیجه</th></tr></thead><tbody></tbody></table>
</div>

<script>
let chart = null;
async function refresh() {
  try {
    const res = await fetch('/api/status');
    const d = await res.json();
    const s = d.risk_state || {};
    const hb = d.heartbeat || {};
    const modeClass = hb.mode === 'live' ? 'badge-live' : 'badge-paper';
    const staleBadge = d.is_stale ? '<span class="badge badge-stale">⚠ داده قدیمی / بات پاسخ نمی‌دهد</span>' : '<span class="badge" style="background:#1e3a2f;color:#86efac;">🟢 آنلاین</span>';

    document.getElementById('statusBar').innerHTML = `
      <div class="card"><div class="label">وضعیت اتصال</div><div class="value">${staleBadge}</div></div>
      <div class="card"><div class="label">حالت</div><div class="value"><span class="badge ${modeClass}">${(hb.mode||'paper').toUpperCase()}</span></div></div>
      <div class="card"><div class="label">مضرب سرمایه</div><div class="value ${d.equity_multiple>=1?'green':'red'}">${d.equity_multiple}x</div></div>
      <div class="card"><div class="label">کل معاملات</div><div class="value">${d.total_trades ?? 0}</div></div>
      <div class="card"><div class="label">نرخ برد</div><div class="value">${d.win_rate_pct ?? '--'}%</div></div>
      <div class="card"><div class="label">معاملات کاغذی</div><div class="value">${s.paper_trades_completed ?? 0} / ${s.paper_trades_required ?? '--'}</div></div>
      <div class="card"><div class="label">PnL امروز</div><div class="value ${(s.daily_pnl_pct??0)>=0?'green':'red'}">${s.daily_pnl_pct ?? 0}%</div></div>
      <div class="card"><div class="label">Circuit Breaker</div><div class="value">${s.circuit_breaker_tripped ? '🔴 فعال' : '🟢 خاموش'}</div></div>
    `;

    const ctx = document.getElementById('equityChart');
    const labels = d.equity_curve.map((_, i) => i);
    if (chart) { chart.data.labels = labels; chart.data.datasets[0].data = d.equity_curve; chart.update(); }
    else {
      chart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets: [{ label: 'Equity (پایه=۱۰۰)', data: d.equity_curve, borderColor: '#22c55e', backgroundColor: 'rgba(34,197,94,0.08)', fill: true, tension: 0.2, pointRadius: 0 }] },
        options: { plugins: { legend: { labels: { color: '#e8eef3' } } }, scales: { x: { ticks: { color: '#8aa0b2' }, grid: { color: '#22303c' } }, y: { ticks: { color: '#8aa0b2' }, grid: { color: '#22303c' } } } }
      });
    }

    const tbody = document.querySelector('#eventsTable tbody');
    tbody.innerHTML = d.recent_events.map(e => `
      <tr>
        <td>${(e.logged_at||'').replace('T',' ').slice(0,19)}</td>
        <td>${e.type}</td>
        <td>${e.mode}</td>
        <td>${e.direction||''}</td>
        <td>${e.entry_price ? Number(e.entry_price).toFixed(2) : (e.exit_price?Number(e.exit_price).toFixed(2):'')}</td>
        <td class="${(e.equity_change_pct||0)>=0?'green':'red'}">${e.reason || ''} ${e.equity_change_pct!=null ? e.equity_change_pct+'%' : ''}</td>
      </tr>`).join('');
  } catch (err) { console.error(err); }
}
refresh();
setInterval(refresh, 15000);
</script>
</body>
</html>
"""
