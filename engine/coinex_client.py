"""
coinex_client.py
=================
کلاینت واقعی API v2 کوینکس (بازار Futures/Perpetual — چون هم Long هم Short نیاز داریم
و اسپات فقط Long پشتیبانی می‌کند). امضا و مسیرها مستقیماً از مستندات رسمی تأیید شده‌اند:
https://docs.coinex.com/api/v2/authorization
https://docs.coinex.com/api/v2/spot/order/http/put-order (ساختار مشابه برای futures)

هرگز access_id / secret_key را در کد ننویسید -- همیشه از متغیر محیطی (.env) بخوانید.
"""
import hashlib
import hmac
import json
import time
import os
import requests

BASE_URL = "https://api.coinex.com/v2"


class CoinExError(Exception):
    pass


class CoinExClient:
    def __init__(self, access_id: str = None, secret_key: str = None, timeout: int = 10):
        self.access_id = access_id or os.environ.get("COINEX_ACCESS_ID")
        self.secret_key = secret_key or os.environ.get("COINEX_SECRET_KEY")
        self.timeout = timeout
        self.session = requests.Session()

    def _sign(self, method: str, request_path: str, body: str, timestamp: str) -> str:
        prepared = f"{method}{request_path}{body}{timestamp}"
        return hmac.new(
            self.secret_key.encode("latin-1"),
            msg=prepared.encode("latin-1"),
            digestmod=hashlib.sha256,
        ).hexdigest().lower()

    def _request(self, method: str, path: str, params: dict = None, body_obj: dict = None, signed: bool = True):
        method = method.upper()
        query = ""
        if params:
            # ترتیب پارامترها باید دقیقاً همانی باشد که در URL نهایی ارسال می‌شود
            query = "?" + "&".join(f"{k}={v}" for k, v in params.items())
        request_path = f"/v2{path}{query}"
        body_str = json.dumps(body_obj, separators=(",", ":")) if body_obj else ""
        url = f"{BASE_URL}{path}{query}"

        headers = {"Content-Type": "application/json"}
        if signed:
            timestamp = str(int(time.time() * 1000))
            sign = self._sign(method, request_path, body_str, timestamp)
            headers.update({
                "X-COINEX-KEY": self.access_id,
                "X-COINEX-SIGN": sign,
                "X-COINEX-TIMESTAMP": timestamp,
            })

        resp = self.session.request(method, url, headers=headers,
                                     data=body_str if body_str else None, timeout=self.timeout)
        try:
            data = resp.json()
        except ValueError:
            raise CoinExError(f"پاسخ غیر-JSON از کوینکس: {resp.status_code} {resp.text[:300]}")
        if data.get("code") != 0:
            raise CoinExError(f"خطای API کوینکس [{data.get('code')}]: {data.get('message')}")
        return data.get("data")

    # ---------------- دادهٔ بازار (بدون نیاز به امضا) ----------------

    def get_klines(self, market: str, period: str = "1hour", limit: int = 700):
        """period طبق مستندات: 1min,3min,5min,15min,30min,1hour,2hour,4hour,6hour,12hour,1day,3day,1week"""
        return self._request("GET", "/futures/kline",
                              params={"market": market, "period": period, "limit": limit}, signed=False)

    def get_ticker(self, market: str):
        return self._request("GET", "/futures/ticker", params={"market": market}, signed=False)

    # ---------------- حساب (نیاز به امضا) ----------------

    def get_futures_balance(self):
        return self._request("GET", "/assets/futures/balance", signed=True)

    def get_positions(self, market: str = None):
        params = {"market": market} if market else {}
        return self._request("GET", "/futures/pending-position", params=params, signed=True)

    def place_market_order(self, market: str, side: str, amount: str, client_id: str = None):
        """side: 'buy' (باز کردن/افزایش Long یا بستن Short) یا 'sell' (باز کردن Short یا بستن Long).
        amount بر حسب مقدار قرارداد (نه دلار) -- طبق قوانین حداقل حجم بازار موردنظر."""
        body = {
            "market": market,
            "market_type": "FUTURES",
            "side": side,
            "type": "market",
            "amount": str(amount),
        }
        if client_id:
            body["client_id"] = client_id
        return self._request("POST", "/futures/order", body_obj=body, signed=True)

    def close_position_market(self, market: str, client_id: str = None):
        """بستن کامل پوزیشن باز با یک سفارش مارکت در جهت مخالف."""
        body = {"market": market, "market_type": "FUTURES"}
        if client_id:
            body["client_id"] = client_id
        return self._request("POST", "/futures/close-position", body_obj=body, signed=True)

    def cancel_all_orders(self, market: str):
        body = {"market": market, "market_type": "FUTURES"}
        return self._request("POST", "/futures/cancel-all-order", body_obj=body, signed=True)

    def set_leverage(self, market: str, leverage: int, margin_mode: str = "isolated"):
        body = {"market": market, "market_type": "FUTURES", "margin_mode": margin_mode, "leverage": leverage}
        return self._request("POST", "/futures/adjust-position-leverage", body_obj=body, signed=True)
