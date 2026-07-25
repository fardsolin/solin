"""
engine/risk_manager.py
========================
دروازهٔ ایمنی. این فایل تنها جایی است که تصمیم می‌گیرد آیا اجازهٔ معاملهٔ واقعی
صادر شود یا نه. طراحی عمداً محافظه‌کارانه است:

  - شروع خودکار همیشه در حالت کاغذی (paper) است.
  - تبدیل به حالت زنده (live) هرگز خودکار نیست -- به این دو شرط نیاز دارد:
      ۱) حداقل MIN_PAPER_TRADES معاملهٔ کاغذی کامل شده باشد (نه صرفاً گذشت زمان)
      ۲) یک تأیید صریح انسانی: فایل CONFIRM_LIVE.txt در پوشهٔ data باید با
         دست ساخته شود و متن دقیق "I_UNDERSTAND_THE_RISK" را داشته باشد.
    این یعنی هیچ باگ یا شرط زمانی به‌تنهایی نمی‌تواند ربات را وارد حالت زنده کند.
  - محدودیت ضرر روزانه: اگر ضرر تجمعی امروز از MAX_DAILY_LOSS_PCT بگذرد، معاملهٔ
    جدید (چه کاغذی چه زنده) تا روز بعد متوقف می‌شود (Circuit Breaker).
  - محدودیت ریسک هر معامله: هرگز بیش از RISK_PER_TRADE_PCT سرمایه به‌خطر نمی‌افتد.
"""
import os
import datetime
import json

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
CONFIRM_FILE = os.path.join(DATA_DIR, "CONFIRM_LIVE.txt")
STATE_FILE = os.path.join(DATA_DIR, "risk_state.json")

MIN_PAPER_TRADES = int(os.environ.get("MIN_PAPER_TRADES", 50))
MAX_DAILY_LOSS_PCT = float(os.environ.get("MAX_DAILY_LOSS_PCT", 0.03))  # ۳٪ سقف ضرر روزانه
RISK_PER_TRADE_PCT = float(os.environ.get("RISK_PER_TRADE_PCT", 0.01))  # ۱٪ ریسک هر معامله
MAX_CONCURRENT_POSITIONS = 1


class RiskManager:
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.state = self._load_state()

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                return json.load(f)
        return {"paper_trades_completed": 0, "daily_pnl_pct": 0.0, "daily_date": str(datetime.date.today()),
                "mode": "paper"}

    def _save_state(self):
        with open(STATE_FILE, "w") as f:
            json.dump(self.state, f, indent=2)

    def _roll_day_if_needed(self):
        today = str(datetime.date.today())
        if self.state["daily_date"] != today:
            self.state["daily_date"] = today
            self.state["daily_pnl_pct"] = 0.0
            self._save_state()

    def record_closed_trade(self, ret_pct_of_equity: float):
        self._roll_day_if_needed()
        self.state["paper_trades_completed"] += 1
        self.state["daily_pnl_pct"] += ret_pct_of_equity
        self._save_state()

    def circuit_breaker_tripped(self) -> bool:
        self._roll_day_if_needed()
        return self.state["daily_pnl_pct"] <= -abs(MAX_DAILY_LOSS_PCT)

    def live_confirmed_by_human(self) -> bool:
        if not os.path.exists(CONFIRM_FILE):
            return False
        with open(CONFIRM_FILE) as f:
            content = f.read().strip()
        return content == "I_UNDERSTAND_THE_RISK"

    def enough_paper_trades(self) -> bool:
        return self.state["paper_trades_completed"] >= MIN_PAPER_TRADES

    def can_go_live(self) -> bool:
        return self.enough_paper_trades() and self.live_confirmed_by_human()

    def current_mode(self) -> str:
        return "live" if self.can_go_live() else "paper"

    def status_report(self) -> dict:
        self._roll_day_if_needed()
        return {
            "mode": self.current_mode(),
            "paper_trades_completed": self.state["paper_trades_completed"],
            "paper_trades_required": MIN_PAPER_TRADES,
            "daily_pnl_pct": round(self.state["daily_pnl_pct"] * 100, 3),
            "max_daily_loss_pct": MAX_DAILY_LOSS_PCT * 100,
            "circuit_breaker_tripped": self.circuit_breaker_tripped(),
            "human_confirmed_live": self.live_confirmed_by_human(),
        }
