"""
bot.py
=======
حلقهٔ اصلی ربات. هر ساعت (وقتی یک کندل ۱ساعتهٔ جدید کاملاً بسته شد) اجرا می‌شود:
  ۱) کندل تازه را می‌گیرد (از API عمومی کوینکس -- نیاز به کلید ندارد)
  ۲) به موتور استراتژی می‌دهد
  ۳) اگر سیگنال ورود/خروج بود، بر اساس حالت فعلی (کاغذی/زنده -- که RiskManager
     تعیین می‌کند) دستور را روی بروکر مناسب اجرا می‌کند
  ۴) نتیجه را در data/trade_log.jsonl می‌نویسد تا داشبورد بخواند
"""
import os
import time
import json
import datetime
import traceback

import pandas as pd

from engine.strategy import StrategyEngine
from engine.risk_manager import RiskManager
from engine.broker import PaperBroker, LiveBroker
from engine.coinex_client import CoinExClient

MARKET = os.environ.get("TRADING_MARKET", "BTCUSDT")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", 60))
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
TRADE_LOG = os.path.join(DATA_DIR, "trade_log.jsonl")
HEARTBEAT_FILE = os.path.join(DATA_DIR, "heartbeat.json")


def log_event(event: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    event["logged_at"] = datetime.datetime.utcnow().isoformat()
    with open(TRADE_LOG, "a") as f:
        f.write(json.dumps(event) + "\n")


def write_heartbeat(extra: dict):
    payload = {"ts": datetime.datetime.utcnow().isoformat(), **extra}
    with open(HEARTBEAT_FILE, "w") as f:
        json.dump(payload, f, indent=2)


def klines_to_df(raw_klines):
    rows = []
    for k in raw_klines:
        rows.append(dict(
            Time=pd.to_datetime(int(k["created_at"]), unit="ms", utc=True),
            Open=float(k["open"]), High=float(k["high"]), Low=float(k["low"]), Close=float(k["close"]),
            Volume=float(k["volume"]),
            # کوینکس Futures kline به‌طور پیش‌فرض تفکیک خرید/فروش تهاجمی نمی‌دهد؛
            # در نبود آن، از یک پروکسی محافظه‌کارانه (نصف حجم) استفاده می‌شود تا سیگنال
            # Whale فقط از داده‌های واقعاً موجود روشن شود -- در صورت وجود دادهٔ order-flow
            # واقعی (مثلاً از یک فید دیگر)، این خط باید با آن جایگزین شود.
            TakerBuyBase=float(k["volume"]) * 0.5,
        ))
    return pd.DataFrame(rows).sort_values("Time").reset_index(drop=True)


def fetch_history(client: CoinExClient, bars: int):
    raw = client.get_klines(MARKET, period="1hour", limit=bars)
    return klines_to_df(raw)


def run():
    print(f"[بات] شروع -- بازار={MARKET}")
    public_client = CoinExClient()  # برای دادهٔ عمومی نیازی به کلید نیست
    risk = RiskManager()
    engine = StrategyEngine()
    paper_broker = PaperBroker(starting_equity=float(os.environ.get("PAPER_START_EQUITY", 10000)))
    live_broker = None  # فقط وقتی واقعاً وارد حالت live شویم ساخته می‌شود (نیاز به کلید واقعی دارد)

    from engine.strategy import MIN_HISTORY_BARS
    print(f"[بات] در حال دریافت {MIN_HISTORY_BARS} کندل تاریخی برای گرم‌کردن موتور...")
    hist = fetch_history(public_client, MIN_HISTORY_BARS + 5)
    engine.load_history(hist)
    last_seen_time = hist["Time"].iloc[-1]
    print(f"[بات] آماده. آخرین کندل دیده‌شده: {last_seen_time}")

    while True:
        try:
            status = risk.status_report()
            mode = status["mode"]
            broker = paper_broker
            if mode == "live":
                if live_broker is None:
                    print("[بات] === ورود به حالت LIVE -- ساخت اتصال واقعی به کوینکس ===")
                    live_broker = LiveBroker(market=MARKET)
                broker = live_broker

            if status["circuit_breaker_tripped"]:
                write_heartbeat({"status": "circuit_breaker_tripped", **status})
                print("[بات] Circuit breaker فعال است -- امروز معاملهٔ جدیدی انجام نمی‌شود.")
                time.sleep(POLL_SECONDS)
                continue

            raw = public_client.get_klines(MARKET, period="1hour", limit=3)
            df_new = klines_to_df(raw)
            newest_closed = df_new.iloc[-2]  # آخرین ایندکس معمولاً کندل نیمه‌کاره است؛ [-2] آخرین کندل کامل

            if newest_closed["Time"] > last_seen_time:
                engine.append_closed_candle(newest_closed.to_dict())
                last_seen_time = newest_closed["Time"]
                result = engine.check_new_bar()
                handle_result(result, engine, broker, risk, status)

            write_heartbeat({"status": "running", "mode": mode, "last_candle": str(last_seen_time), **status})

        except Exception as e:
            print(f"[بات][خطا] {e}")
            traceback.print_exc()
            write_heartbeat({"status": "error", "error": str(e)})

        time.sleep(POLL_SECONDS)


def handle_result(result, engine, broker, risk, status):
    action = result.get("action")
    if action == "enter":
        equity = broker.get_equity()
        risk_amount = equity * risk.state.get("risk_per_trade_pct", 0.01)
        stop_dist = abs(result["entry_price_ref"] - result["stop"])
        size = risk_amount / stop_dist if stop_dist > 0 else 0
        if size <= 0:
            return
        fill = broker.open_position(MARKET, result["direction"], size, result["entry_price_ref"])
        engine.open_position(result["direction"], fill["fill_price"], result["stop"], result["target"])
        log_event({"type": "enter", "mode": ("live" if broker.is_live else "paper"),
                   "direction": result["direction"], "signal_source": result.get("signal_source"),
                   "entry_price": fill["fill_price"], "stop": result["stop"], "target": result["target"],
                   "size": size, "order_id": fill.get("order_id")})
        print(f"[بات] ورود {result['direction']} @ {fill['fill_price']} (منبع: {result.get('signal_source')}, حالت: {'live' if broker.is_live else 'paper'})")

    elif action == "exit":
        direction = result["direction"]
        ret_pct = ((result["exit_price"] / result["entry_price"]) - 1) if direction == "long" else \
                  ((result["entry_price"] / result["exit_price"]) - 1)
        risk_pct_of_trade = abs(result["entry_price"] - result["initial_stop"]) / result["entry_price"]
        r_multiple = ret_pct / risk_pct_of_trade if risk_pct_of_trade > 0 else 0
        equity_change_pct = 0.01 * max(min(r_multiple, 10), -1.5)  # همان سقف واقع‌بینانه‌ای که در بک‌تست استفاده شد

        fill = broker.close_position(MARKET, direction, 0, result["exit_price"])
        if not broker.is_live:
            broker.apply_pnl(broker.equity * equity_change_pct)
        risk.record_closed_trade(equity_change_pct)
        log_event({"type": "exit", "mode": ("live" if broker.is_live else "paper"),
                   "direction": direction, "reason": result["reason"], "exit_price": result["exit_price"],
                   "entry_price": result["entry_price"], "r_multiple": round(r_multiple, 3),
                   "equity_change_pct": round(equity_change_pct * 100, 3),
                   "equity_after": broker.get_equity() if not broker.is_live else None})
        print(f"[بات] خروج {direction} @ {result['exit_price']} دلیل={result['reason']} R={r_multiple:.2f}")


if __name__ == "__main__":
    run()
