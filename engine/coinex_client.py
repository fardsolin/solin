"""Allowlisted PUBLIC market-data client. All private operations fail before I/O."""

import json

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import LiveTradingDisabled

BASE_URL = "https://api.coinex.com/v2"


class CoinExError(RuntimeError):
    pass


class CoinExClient:
    def __init__(self, access_id=None, secret_key=None, timeout=10):
        if access_id or secret_key:
            raise LiveTradingDisabled("Do not provide exchange credentials to this paper-only release")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False  # Do not pick up netrc authentication or ambient proxies.
        retry = Retry(
            total=3, backoff_factor=0.5, allowed_methods={"GET"}, status_forcelist=[429, 500, 502, 503, 504]
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def _request(self, method, path, params=None, body_obj=None, signed=False):
        if signed or method != "GET" or path not in {"/futures/kline", "/futures/ticker"} or body_obj:
            raise LiveTradingDisabled("Private/write exchange operations are disabled")
        with self.session.request(
            "GET",
            BASE_URL + path,
            params=params,
            timeout=(5, self.timeout),
            allow_redirects=False,
            stream=True,
        ) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise CoinExError("Unexpected public API HTTP status")
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                size += len(chunk)
                if size > 4_000_000:
                    raise CoinExError("Oversized public API response")
                chunks.append(chunk)
            try:
                payload = json.loads(b"".join(chunks))
            except (ValueError, RecursionError) as e:
                raise CoinExError("Invalid JSON from public market-data API") from e
        if (
            not isinstance(payload, dict)
            or payload.get("code") != 0
            or not isinstance(payload.get("data"), list)
        ):
            raise CoinExError("Invalid/error public market-data response")
        return payload["data"]

    def get_klines(self, market="BTCUSDT", period="1hour", limit=805, start_time=None, end_time=None):
        if market != "BTCUSDT" or period != "1hour" or not 1 <= limit <= 1000:
            raise ValueError("Unsupported candle request")
        params = {"market": market, "period": period, "limit": limit}
        if start_time is not None:
            params["start_time"] = int(start_time)
        if end_time is not None:
            params["end_time"] = int(end_time)
        return self._request("GET", "/futures/kline", params=params)

    def get_ticker(self, market="BTCUSDT"):
        return self._request("GET", "/futures/ticker", params={"market": market})

    def get_futures_balance(self):
        return self._request("GET", "/assets/futures/balance", signed=True)

    def get_positions(self, market="BTCUSDT"):
        return self._request(
            "GET",
            "/futures/pending-position",
            params={"market": market, "market_type": "FUTURES"},
            signed=True,
        )

    def place_market_order(self, market, side, amount, client_id=None):
        body = {
            "market": market,
            "market_type": "FUTURES",
            "type": "market",
            "side": side,
            "amount": str(amount),
            "client_id": client_id,
        }
        return self._request("POST", "/futures/order", body_obj=body, signed=True)

    def close_position_market(self, market, client_id=None):
        body = {"market": market, "market_type": "FUTURES", "type": "market"}
        if client_id:
            body["client_id"] = client_id
        return self._request("POST", "/futures/close-position", body_obj=body, signed=True)

    def cancel_all_orders(self, market):
        return self._request(
            "POST",
            "/futures/cancel-all-order",
            body_obj={"market": market, "market_type": "FUTURES"},
            signed=True,
        )

    def set_leverage(self, market, leverage, margin_mode="isolated"):
        return self._request("POST", "/futures/adjust-position-leverage", signed=True)
