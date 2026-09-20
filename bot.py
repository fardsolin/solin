"""Paper-only hourly simulation. Public data, persistent recovery, no private orders."""

import argparse
import logging
import signal
import threading
import time

from dotenv import load_dotenv

from engine.coinex_client import CoinExClient
from engine.config import Settings
from engine.market_data import fetch_closed
from engine.session import TradingSession
from engine.storage import StateStore, writer_lock
from engine.strategy import MIN_HISTORY_BARS

log = logging.getLogger(__name__)


def reject_legacy_state(data_dir):
    for name in ["CONFIRM_LIVE.txt", "risk_state.json", "trade_log.jsonl"]:
        if (data_dir / name).exists():
            raise RuntimeError(
                "Legacy state detected. Verify/close any real positions at the exchange and "
                "archive legacy files manually; automatic migration is not safe."
            )


def run(once=False):
    settings = Settings.from_env()
    reject_legacy_state(settings.data_dir)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    with writer_lock(settings.data_dir):
        store = StateStore(settings.data_dir / "paper.sqlite3")
        try:
            revision, saved = store.load()
            session = TradingSession(settings.simulation, saved)
            session.revision = revision
            client = CoinExClient()
            while not stop.is_set():
                try:
                    now_ms = int(time.time() * 1000)
                    candles = fetch_closed(client, session.last_time, now_ms)
                    if session.last_time is None:
                        if len(candles) < MIN_HISTORY_BARS:
                            raise ValueError("Not enough closed candles for warmup")
                        candidate = TradingSession(settings.simulation)
                        candidate.warmup(candles)
                        candidate.revision = store.save(candidate.snapshot(), expected_revision=0)
                        session = candidate
                    else:
                        for candle in candles:
                            session.process(candle, store=store)
                    store.heartbeat(
                        {
                            "ts_ms": int(time.time() * 1000),
                            "status": "running",
                            "source": "CoinEx public hourly candles",
                            "funding_model": "not included in polling simulation",
                            **session.status(),
                        }
                    )
                except Exception:
                    log.exception("Paper loop failed; no new checkpoint for failed work")
                    store.heartbeat(
                        {
                            "ts_ms": int(time.time() * 1000),
                            "status": "error",
                            "error": "Data/state processing failed; inspect private server logs",
                            **session.status(),
                        }
                    )
                    if once:
                        raise
                if once:
                    return session.status()
                stop.wait(settings.poll_seconds)
        finally:
            store.close()


if __name__ == "__main__":
    load_dotenv(override=False)
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Process one public-data poll and exit")
    print(run(once=parser.parse_args().once))
