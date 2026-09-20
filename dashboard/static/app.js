'use strict';
const number = value => value == null ? '—' : Number(value).toLocaleString('en-US', { maximumFractionDigits: 4 });
const cell = text => { const td = document.createElement('td'); td.textContent = String(text ?? '—'); return td; };
async function refresh() {
  const connection = document.getElementById('connection');
  try {
    const response = await fetch('/api/status', { credentials: 'same-origin', cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const state = data.heartbeat || {};
    connection.textContent = data.healthy ? 'داده تازه / شبیه‌ساز در حال اجرا' : 'هشدار: بات خطا دارد یا دادهٔ بازار کهنه/ناموجود است';
    connection.className = data.healthy ? 'good' : 'warning';
    const values = [['حالت', 'PAPER — Live disabled'], ['ارزش حساب شبیه‌سازی', number(state.equity)],
      ['موجودی نقد شبیه‌سازی', number(state.cash)], ['معاملات بسته', number(state.paper_trades_completed)],
      ['نرخ برد', `${number(state.win_rate_pct)}%`], ['PnL روزانه', `${number(state.daily_pnl_pct)}%`],
      ['توقف ورود جدید', state.circuit_breaker_tripped == null ? 'نامشخص' : state.circuit_breaker_tripped ? 'فعال' : 'غیرفعال'],
      ['کارمزد تجمعی', number(state.fees_paid)]];
    const cards = document.getElementById('cards'); cards.replaceChildren();
    for (const [label, value] of values) {
      const card = document.createElement('article'); const title = document.createElement('span');
      const body = document.createElement('strong'); title.textContent = label; body.textContent = value;
      card.append(title, body); cards.append(card);
    }
    document.getElementById('position').textContent = state.position ? JSON.stringify(state.position, null, 2) : 'پوزیشن باز ثبت نشده است';
    const events = document.getElementById('events'); events.replaceChildren();
    for (const event of data.recent_events || []) {
      const row = document.createElement('tr');
      [new Date(event.time).toISOString(), event.type, event.direction,
       number(event.type === 'exit' ? event.exit_price : event.entry_price), number(event.net_pnl), event.exit_reason || event.reason]
        .forEach(value => row.append(cell(value)));
      events.append(row);
    }
  } catch (_) {
    connection.textContent = 'ارتباط یا احراز هویت ناموفق است؛ مقادیر روی صفحه قابل اتکا نیستند.';
    connection.className = 'warning';
    document.getElementById('cards').replaceChildren();
    document.getElementById('position').textContent = 'وضعیت نامعلوم';
    document.getElementById('events').replaceChildren();
  }
}
refresh(); setInterval(refresh, 15000);
