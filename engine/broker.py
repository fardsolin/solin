"""
engine/broker.py
==================
انتزاع بروکر: هم برای معاملهٔ کاغذی (تمرینی) هم برای معاملهٔ واقعی، رابط یکسان
دارند تا موتور استراتژی نداند در کدام حالت است.
"""
import time
import uuid
from .coinex_client import CoinExClient


class PaperBroker:
    """معاملهٔ کاغذی: هیچ سفارش واقعی ثبت نمی‌شود، فقط اجرا شبیه‌سازی می‌شود."""

    def __init__(self, starting_equity: float = 10_000.0):
        self.equity = starting_equity
        self.is_live = False

    def get_equity(self):
        return self.equity

    def open_position(self, market, direction, size_contracts, ref_price):
        return {"status": "filled", "fill_price": ref_price, "order_id": f"paper-{uuid.uuid4().hex[:8]}"}

    def close_position(self, market, direction, size_contracts, ref_price):
        return {"status": "filled", "fill_price": ref_price, "order_id": f"paper-{uuid.uuid4().hex[:8]}"}

    def apply_pnl(self, pnl_amount: float):
        self.equity += pnl_amount


class LiveBroker:
    """معاملهٔ واقعی روی کوینکس فیوچرز. فقط بعد از عبور از دروازهٔ ایمنی
    (ریسک_منیجر) صدا زده می‌شود."""

    def __init__(self, market: str = "BTCUSDT", leverage: int = 2):
        self.client = CoinExClient()
        self.market = market
        self.leverage = leverage
        self.is_live = True
        try:
            self.client.set_leverage(market, leverage)
        except Exception as e:
            print(f"[هشدار] تنظیم اهرم ناموفق بود (ادامه می‌دهیم با تنظیمات فعلی حساب): {e}")

    def get_equity(self):
        bal = self.client.get_futures_balance()
        usdt = next((b for b in bal if b.get("ccy") == "USDT"), None)
        if not usdt:
            raise RuntimeError("موجودی USDT در حساب فیوچرز یافت نشد")
        return float(usdt["available"]) + float(usdt["frozen"])

    def open_position(self, market, direction, size_contracts, ref_price):
        side = "buy" if direction == "long" else "sell"
        client_id = f"bot-{uuid.uuid4().hex[:12]}"
        result = self.client.place_market_order(market, side, size_contracts, client_id=client_id)
        return {"status": "submitted", "fill_price": result.get("last_fill_price", ref_price),
                "order_id": result.get("order_id"), "raw": result}

    def close_position(self, market, direction, size_contracts, ref_price):
        result = self.client.close_position_market(market)
        return {"status": "submitted", "fill_price": ref_price, "order_id": None, "raw": result}
