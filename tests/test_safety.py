from dataclasses import replace
from decimal import Decimal
import json
import math
from pathlib import Path
import sqlite3
import time
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from bot import reject_legacy_state
from dashboard.server import create_app
from engine.broker import LiveBroker, PaperBroker
from engine.coinex_client import CoinExClient
from engine.config import LiveTradingDisabled, Settings, SimulationConfig
from engine.market_data import fetch_closed, parse_closed
from engine.models import Candle, HOUR_MS
from engine.risk_manager import RiskManager
from engine.session import TradingSession
from engine.storage import StateStore, read_status, writer_lock
from engine.strategy import StrategyEngine, percentile_rank
from research.backtest import metrics, run_backtest
from research.data import read_candles, read_funding

T0 = 1704067200000  # 2024-01-01 00:00 UTC
ZERO_COST = SimulationConfig(fee_bps=0, slippage_bps=0)
PASSWORD = "unit-test-password-not-a-real-secret-012345"


def candle(i=0, **kwargs):
    values = dict(
        time=T0 + i * HOUR_MS, open=100.0, high=100.2, low=99.8, close=100.0, volume=10.0, taker_buy=5.0
    )
    values.update(kwargs)
    return Candle(**values)


def signal(i=0, direction="long", stop=None):
    return {
        "signal_time": T0 + (i - 1) * HOUR_MS,
        "direction": direction,
        "stop": stop if stop is not None else 90.0 if direction == "long" else 110.0,
        "signal_source": "breakout",
        "reference_price": 100.0,
    }


def seeded(config=ZERO_COST, n=1200):
    session = TradingSession(config)
    session.warmup([candle(i) for i in range(n)])
    return session


def trend(n):
    return [
        candle(
            i,
            open=100 + i / 100,
            high=100.1 + i / 100,
            low=99.9 + i / 100,
            close=100 + i / 100,
            volume=1000 if i % 64 == 47 else 10,
            taker_buy=900 if i % 64 == 47 else 5,
        )
        for i in range(n)
    ]


@pytest.mark.parametrize("mode", ["live", "LIVE", "real", "automatic", ""])
def test_live_config_cannot_be_enabled(monkeypatch, mode):
    monkeypatch.setenv("TRADING_MODE", mode)
    with pytest.raises(LiveTradingDisabled):
        Settings.from_env()


def test_environment_risk_is_applied(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("RISK_PER_TRADE_PCT", "0.02")
    assert Settings.from_env().simulation.risk_per_trade == 0.02


@pytest.mark.parametrize(
    "changes",
    [
        dict(risk_per_trade=math.nan),
        dict(risk_per_trade=0),
        dict(risk_per_trade=0.5),
        dict(starting_equity=-1),
        dict(fee_bps=-1),
        dict(slippage_bps=101),
        dict(max_leverage=10),
        dict(quantity_step=0),
        dict(max_daily_loss=math.inf),
        dict(hold_bars=0),
    ],
)
def test_invalid_config_rejected(changes):
    with pytest.raises(ValueError):
        SimulationConfig(**changes)


def test_live_broker_disabled_even_with_confirmation(tmp_path, monkeypatch):
    (tmp_path / "CONFIRM_LIVE.txt").write_text("I_UNDERSTAND_THE_RISK")
    monkeypatch.setenv("COINEX_ACCESS_ID", "not-a-real-key")
    monkeypatch.setenv("COINEX_SECRET_KEY", "not-a-real-secret")
    with pytest.raises(LiveTradingDisabled):
        LiveBroker()
    with pytest.raises(RuntimeError, match="Legacy"):
        reject_legacy_state(tmp_path)


@pytest.mark.parametrize(
    "name,args",
    [
        ("get_futures_balance", ()),
        ("get_positions", ()),
        ("place_market_order", ("BTCUSDT", "buy", 1)),
        ("close_position_market", ("BTCUSDT",)),
        ("cancel_all_orders", ("BTCUSDT",)),
        ("set_leverage", ("BTCUSDT", 2)),
    ],
)
def test_every_private_client_operation_fails_before_network(name, args):
    with pytest.raises(LiveTradingDisabled):
        getattr(CoinExClient(), name)(*args)


def test_credentials_are_not_accepted():
    with pytest.raises(LiveTradingDisabled):
        CoinExClient("not-a-real-key", "not-a-real-secret")


def test_future_private_contract_fields_are_correct_but_gated():
    client = CoinExClient()
    client._request = Mock(return_value={})
    client.close_position_market("BTCUSDT")
    assert client._request.call_args.kwargs["body_obj"]["type"] == "market"
    client.get_positions()
    assert client._request.call_args.kwargs["params"]["market_type"] == "FUTURES"


@pytest.mark.parametrize("direction,stop,exit_price", [("long", 90, 110), ("short", 110, 90)])
def test_linear_quantity_based_pnl_and_configured_risk(direction, stop, exit_price):
    broker = PaperBroker(replace(ZERO_COST, risk_per_trade=0.02))
    broker.open_position(signal(direction=direction, stop=stop), candle())
    assert broker.position.quantity == 20
    result = broker.close_position(exit_price, T0 + HOUR_MS, "test")
    assert result["net_pnl"] == 200
    assert broker.cash == 10200
    assert broker.position is None


def test_gap_loss_is_not_clipped_to_one_and_half_r():
    broker = PaperBroker(ZERO_COST)
    broker.open_position(signal(stop=95), candle())
    result = broker.close_position(80, T0 + HOUR_MS, "gap")
    assert result["net_pnl"] == -400
    assert result["r_multiple_net"] == -4
    assert broker.cash == 9600


def test_costs_and_quantity_rounding_included_in_risk_budget():
    broker = PaperBroker(SimulationConfig(quantity_step=0.001))
    broker.open_position(signal(), candle())
    p = broker.position
    stop_fill = broker.fill_price(p.initial_stop, 1, False)
    risk_per_unit = p.entry_price - stop_fill + (p.entry_price + stop_fill) * 0.0006
    assert p.quantity * risk_per_unit <= 100 + 1e-8
    assert Decimal(str(p.quantity)) % Decimal("0.001") == 0
    result = broker.close_position(110, T0 + HOUR_MS, "test")
    assert result["exit_price"] < 110
    assert p.entry_price > 100
    assert broker.cash == pytest.approx(10000 + result["net_pnl"], abs=1e-7)
    assert broker.fees_paid == pytest.approx(p.entry_fee + result["exit_fee"])


def test_max_notional_and_single_position_limits():
    broker = PaperBroker(replace(ZERO_COST, max_leverage=1))
    broker.open_position(signal(stop=99.99), candle())
    assert broker.position.quantity * broker.position.entry_price <= 10000
    with pytest.raises(ValueError, match="one"):
        broker.open_position(signal(), candle())


def test_failed_exit_does_not_forget_position(monkeypatch):
    broker = PaperBroker(ZERO_COST)
    broker.open_position(signal(), candle())
    before = broker.snapshot()
    monkeypatch.setattr(broker, "fill_price", Mock(side_effect=TimeoutError))
    with pytest.raises(TimeoutError):
        broker.close_position(90, T0, "test")
    assert broker.snapshot() == before


def test_old_stop_before_new_trail_no_profitable_hindsight():
    broker = PaperBroker(ZERO_COST)
    broker.open_position(signal(stop=95), candle())
    result = broker.manage_bar(candle(high=101, low=94))
    assert result["exit_price"] == 95
    assert result["net_pnl"] == -100


def test_trailing_only_becomes_effective_on_next_bar():
    broker = PaperBroker(ZERO_COST)
    broker.open_position(signal(stop=95), candle())
    assert broker.manage_bar(candle(high=101, low=99.8)) is None
    assert broker.position.current_stop == pytest.approx(100.9)
    result = broker.manage_bar(candle(1, open=101, high=101.1, low=100.7, close=101))
    assert result["exit_price"] == pytest.approx(100.9)


def test_ambiguous_stop_and_target_uses_stop_first():
    broker = PaperBroker(ZERO_COST)
    broker.open_position(signal(), candle())
    result = broker.manage_bar(candle(high=130, low=85))
    assert result["exit_price"] == 90


def test_gap_executes_at_open_not_stale_stop():
    broker = PaperBroker(ZERO_COST)
    broker.open_position(signal(), candle())
    result = broker.manage_bar(candle(1, open=80, high=89, low=75, close=85))
    assert result["exit_price"] == 80
    assert result["exit_reason"] == "stop-gap"


def test_entry_gap_through_stop_is_rejected():
    broker = PaperBroker(ZERO_COST)
    result = broker.open_position(signal(stop=95), candle(open=90, high=91, low=89, close=90))
    assert result["type"] == "entry_rejected"
    assert broker.position is None


def test_stale_entry_signal_is_rejected():
    with pytest.raises(ValueError, match="preceding"):
        PaperBroker(ZERO_COST).open_position(signal(), candle(5))


def test_time_exit_after_buffer_length_1000_has_been_exceeded():
    session = seeded()
    session.pending = signal(1200)
    events = []
    for i in range(1200, 1211):
        events.extend(session.process(candle(i)))
    exits = [e for e in events if e["type"] == "exit"]
    assert len(exits) == 1
    assert exits[0]["exit_reason"] == "time-exit"
    assert exits[0]["bars_held"] == 10
    assert exits[0]["time"] == T0 + 1210 * HOUR_MS


def test_breaker_latches_but_never_blocks_exits():
    session = seeded()
    session.pending = signal(1200)
    session.process(candle(1200))
    session.risk.tripped = True
    exits = session.process(candle(1201, low=89, close=95))
    assert any(e["type"] == "exit" for e in exits)
    assert session.broker.position is None
    assert session.status()["live_enabled"] is False


def test_daily_risk_utc_reset_and_display_units():
    risk = RiskManager()
    risk.roll_day(T0, 10000)
    risk.observe(9700)
    assert risk.tripped
    risk.observe(10100)
    assert risk.tripped  # No same-day reset on recovery.
    risk.roll_day(T0 + 24 * HOUR_MS, 10100)
    assert not risk.tripped
    risk.observe(9797)
    assert risk.daily_pnl_pct * 100 == pytest.approx(-3)


@pytest.mark.parametrize("direction,sign", [("long", -1), ("short", 1)])
def test_signed_funding_is_counted_once_in_cash_and_trade_pnl(direction, sign):
    broker = PaperBroker(ZERO_COST)
    broker.open_position(signal(direction=direction), candle())
    broker.apply_funding(0.001, 100, T0 + HOUR_MS)
    trade = broker.close_position(100, T0 + 2 * HOUR_MS, "test")
    assert trade["net_pnl"] == sign * 1
    assert broker.cash == 10000 + sign * 1


@pytest.mark.parametrize("values,expected", [([1, 1, 1], 2 / 3), ([1, 2, 2], 2.5 / 3), ([3, 2, 1], 1 / 3)])
def test_percentile_tie_contract(values, expected):
    assert percentile_rank(values) == pytest.approx(expected)


def test_h4_only_publishes_complete_utc_groups():
    engine = StrategyEngine()
    for i in [1, 2, 3]:
        engine.update(candle(i))
    assert engine.ema_slow is None
    for i in [4, 5, 6]:
        engine.update(candle(i))
    assert engine.ema_slow is None
    engine.update(candle(7, close=100.1))
    assert engine.ema_slow == 100.1


def test_whale_requires_real_flow_and_can_generate_signal():
    disabled, enabled = StrategyEngine(False), StrategyEngine(True)
    rows = trend(1200)
    # Monday 23:00: actual, exceptional buy flow; no breakout body.
    rows[-1] = replace(rows[-1], volume=10000, taker_buy=9000)
    left = right = None
    for c in rows:
        left, right = disabled.update(c), enabled.update(c)
    assert left is None
    assert right["signal_source"] == "whale"
    with pytest.raises(ValueError, match="genuine"):
        StrategyEngine(True).update(candle(taker_buy=None))


def test_indicator_restart_and_prefix_causality_are_exact():
    rows = trend(1800)
    engine = StrategyEngine(True)
    first = [engine.update(c) for c in rows[:1200]]
    restored = StrategyEngine(True, json.loads(json.dumps(engine.snapshot())))
    assert [engine.update(c) for c in rows[1200:]] == [restored.update(c) for c in rows[1200:]]
    assert engine.snapshot() == restored.snapshot()
    prefix = StrategyEngine(True)
    assert [prefix.update(c) for c in rows[:1200]] == first
    assert len(engine.prior) == 20 and len(engine.widths) == 250 and len(engine.strengths) == 500


@pytest.mark.parametrize(
    "changes",
    [
        dict(high=99),
        dict(low=101),
        dict(close=math.nan),
        dict(volume=-1),
        dict(taker_buy=20),
        dict(time=T0 + 1),
        dict(open=0),
    ],
)
def test_bad_candles_fail_validation(changes):
    with pytest.raises(ValueError):
        candle(**changes)


def raw(c):
    return {
        "created_at": c.time,
        **{k: str(getattr(c, k)) for k in ["open", "high", "low", "close", "volume"]},
    }


def test_partial_warmup_candle_is_excluded_not_marked_seen():
    rows = parse_closed([raw(candle(0)), raw(candle(1)), raw(candle(2))], T0 + 2 * HOUR_MS + 6000)
    assert [c.time for c in rows] == [T0, T0 + HOUR_MS]
    assert all(c.taker_buy is None for c in rows)  # Never synthesize half-volume.


def test_backfill_includes_every_missed_candle_and_paginates():
    client = Mock()
    client.get_klines.side_effect = lambda **kw: [
        raw(candle(i))
        for i in range((kw["start_time"] - T0) // HOUR_MS, (kw["end_time"] - T0) // HOUR_MS + 1)
    ]
    rows = fetch_closed(client, T0, T0 + 1006 * HOUR_MS + 6000)
    assert len(rows) == 1005
    assert client.get_klines.call_count == 2
    assert rows[0].time == T0 + HOUR_MS


def test_gapped_public_response_rejected():
    with pytest.raises(ValueError):
        parse_closed([raw(candle(0)), raw(candle(2))], T0 + 5 * HOUR_MS)


def test_exact_duplicate_is_idempotent_changed_duplicate_rejected():
    session = seeded()
    before = session.snapshot()
    assert session.process(candle(1199)) == []
    assert session.snapshot() == before
    with pytest.raises(ValueError, match="changed"):
        session.process(candle(1199, close=100.1))
    with pytest.raises(ValueError, match="Missing"):
        session.process(candle(1201))


def test_checkpoint_recovers_equity_position_indicators_and_pending(tmp_path):
    store = StateStore(tmp_path / "paper.sqlite3")
    session = seeded()
    session.pending = signal(1200)
    session.process(candle(1200), store=store)
    revision, state = store.load()
    recovered = TradingSession(ZERO_COST, state)
    recovered.revision = revision
    assert recovered.snapshot() == session.snapshot()
    assert recovered.broker.position.quantity == 10
    c = candle(1201, low=89, close=95)
    expected = session.process(c)
    assert recovered.process(c, store=store) == expected
    assert recovered.snapshot() == session.snapshot()
    assert recovered.broker.cash == 9900
    store.close()


def test_failed_storage_commit_does_not_advance_in_memory_state(tmp_path, monkeypatch):
    store = StateStore(tmp_path / "paper.sqlite3")
    session = seeded()
    session.pending = signal(1200)
    before = session.snapshot()
    monkeypatch.setattr(store, "save", Mock(side_effect=sqlite3.OperationalError("disk full")))
    with pytest.raises(sqlite3.OperationalError):
        session.process(candle(1200), store=store)
    assert session.snapshot() == before
    assert store.load() == (0, None)
    store.close()


def test_journal_and_checkpoint_roll_back_together(tmp_path):
    store = StateStore(tmp_path / "paper.sqlite3")
    state = seeded(n=800).snapshot()
    event = {"time": T0, "type": "test", "trade_id": "test"}
    assert store.save(state, [event]) == 1
    with pytest.raises(sqlite3.IntegrityError):
        store.save(state, [event], expected_revision=1)
    assert store.load()[0] == 1
    with pytest.raises(RuntimeError, match="Stale"):
        store.save(state, expected_revision=0)
    store.close()


def test_changed_config_or_mode_never_silently_resets_state():
    state = seeded().snapshot()
    with pytest.raises(ValueError, match="mismatch"):
        TradingSession(replace(ZERO_COST, risk_per_trade=0.02), state)
    state["mode"] = "live"
    with pytest.raises(ValueError, match="mismatch"):
        TradingSession(ZERO_COST, state)


def test_single_writer_lock(tmp_path):
    with writer_lock(tmp_path):
        with pytest.raises(RuntimeError, match="Another"):
            with writer_lock(tmp_path):
                pass


def test_read_only_status_does_not_create_database(tmp_path):
    assert read_status(tmp_path / "absent.sqlite3") == {"heartbeat": {}, "recent_events": []}
    assert not (tmp_path / "absent.sqlite3").exists()


def test_dashboard_authentication_and_fail_closed_password(tmp_path):
    with TestClient(create_app(tmp_path, PASSWORD)) as client:
        assert client.get("/api/status").status_code == 401
        assert client.get("/").status_code == 401
        assert client.get("/api/status", auth=("admin", "wrong")).status_code == 401
        assert client.get("/api/status", auth=("admin", PASSWORD)).status_code == 200
        assert client.get("/healthz").json() == {"service": "dashboard", "ok": True}
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/api/status", auth=("admin", PASSWORD), params={"limit": 10000}).status_code == 422
    with TestClient(create_app(tmp_path, "")) as client:
        assert client.get("/api/status").status_code == 503


@pytest.mark.parametrize(
    "status,age,healthy", [("running", 1, True), ("error", 1, False), ("running", 24, False)]
)
def test_dashboard_distinguishes_error_process_and_market_staleness(tmp_path, status, age, healthy):
    store = StateStore(tmp_path / "paper.sqlite3")
    now = int(time.time() * 1000)
    store.heartbeat(
        {
            "status": status,
            "ts_ms": now,
            "last_candle_open_ms": (now // HOUR_MS - age) * HOUR_MS,
            "daily_pnl_pct": -3.0,
            "circuit_breaker_tripped": True,
        }
    )
    with TestClient(create_app(tmp_path, PASSWORD)) as client:
        response = client.get("/api/status", auth=("admin", PASSWORD))
        data = response.json()
        assert data["healthy"] is healthy
        assert data["heartbeat"]["daily_pnl_pct"] == -3
        assert data["heartbeat"]["circuit_breaker_tripped"] is True
        assert response.headers["cache-control"] == "no-store"
        assert "script-src 'self'" in response.headers["content-security-policy"]
    store.close()


def test_corrupt_dashboard_state_is_503_not_fake_healthy(tmp_path):
    (tmp_path / "paper.sqlite3").write_bytes(b"not a sqlite database")
    with TestClient(create_app(tmp_path, PASSWORD), raise_server_exceptions=False) as client:
        response = client.get("/api/status", auth=("admin", PASSWORD))
        assert response.status_code == 503
        assert response.json()["healthy"] is False


def test_dashboard_has_no_unsafe_html_injection_or_external_scripts():
    root = Path(__file__).parents[1]
    assert "innerHTML" not in (root / "dashboard/static/app.js").read_text()
    assert "https://" not in (root / "dashboard/static/index.html").read_text()


def test_backtest_accounting_metrics_include_initial_drawdown():
    curve = [
        {"time": T0 + (i + 1) * HOUR_MS, "equity": eq, "exposed": True}
        for i, eq in enumerate([1100, 1050, 1075])
    ]
    trades = [{"net_pnl": p, "direction": "long"} for p in [100, -50, 25]]
    result = metrics(trades, curve, 1000, 0, 0)
    assert result["profit_factor_net"] == 2.5
    assert result["net_return_pct"] == pytest.approx(7.5)
    assert result["max_drawdown_close_to_close_pct"] == pytest.approx(-100 * 50 / 1100)


def test_backtest_zero_trades_is_valid_not_infinite_profit_factor():
    result = run_backtest([candle(i) for i in range(820)], ZERO_COST)
    assert result["summary"]["trades"] == 0
    assert result["summary"]["profit_factor_net"] is None
    assert result["summary"]["net_return_pct"] == 0


def test_backtest_is_deterministic_and_reconciles_cash_and_ledger():
    rows = trend(1600)
    config = SimulationConfig(enable_whale=True)
    first = run_backtest(rows, config)
    second = run_backtest(rows, config)
    assert first == second
    assert first["trades"]
    assert first["summary"]["final_equity"] == pytest.approx(
        10000 + sum(t["net_pnl"] for t in first["trades"]), abs=1e-5
    )
    assert first["summary"]["fees_paid"] > 0


def test_dataset_rejects_gaps_and_future_candles(tmp_path):
    path = tmp_path / "bars.csv"
    path.write_text(
        "time,open,high,low,close,volume\n"
        + f"{T0},100,101,99,100,10\n{T0 + 2 * HOUR_MS},100,101,99,100,10\n"
    )
    with pytest.raises(ValueError, match="gap"):
        read_candles(path)
    with pytest.raises(ValueError, match="Future"):
        read_candles(path, as_of_ms=T0)


def test_funding_input_rejects_duplicates(tmp_path):
    path = tmp_path / "funding.csv"
    path.write_text(f"time,rate\n{T0},0.0001\n{T0},0.0001\n")
    with pytest.raises(ValueError):
        read_funding(path)


def test_incomplete_historical_funding_is_not_silently_zero_filled():
    with pytest.raises(ValueError, match="funding coverage"):
        run_backtest([candle(i) for i in range(820)], ZERO_COST, funding={})


def test_dashboard_connection_is_closed_after_read(tmp_path, monkeypatch):
    store = StateStore(tmp_path / "paper.sqlite3")
    real_connect = sqlite3.connect
    opened = []

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    read_status(tmp_path / "paper.sqlite3")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")
    store.close()


def test_malformed_status_schema_is_unavailable(tmp_path):
    store = StateStore(tmp_path / "paper.sqlite3")
    store.heartbeat([])
    with TestClient(create_app(tmp_path, PASSWORD)) as client:
        assert client.get("/api/status", auth=("admin", PASSWORD)).status_code == 503
    store.close()


def test_public_client_has_no_ambient_auth_and_uses_bounded_stream(monkeypatch):
    from unittest.mock import MagicMock

    client = CoinExClient()
    assert client.session.trust_env is False
    response = MagicMock()
    response.__enter__.return_value = response
    response.status_code = 200
    response.iter_content.return_value = [b'{"code":0,"data":[]}']
    request = Mock(return_value=response)
    monkeypatch.setattr(client.session, "request", request)
    assert client.get_klines() == []
    assert request.call_args.kwargs["stream"] is True
    assert request.call_args.kwargs["allow_redirects"] is False
    assert "headers" not in request.call_args.kwargs


def test_public_client_rejects_oversized_response(monkeypatch):
    from unittest.mock import MagicMock
    from engine.coinex_client import CoinExError

    client = CoinExClient()
    response = MagicMock()
    response.__enter__.return_value = response
    response.status_code = 200
    response.iter_content.return_value = [b"x" * 65536] * 62
    monkeypatch.setattr(client.session, "request", Mock(return_value=response))
    with pytest.raises(CoinExError, match="Oversized"):
        client.get_klines()


def test_checkpoint_and_legacy_snapshot_have_no_live_auto_promotion():
    session = seeded()
    session.broker.closed_trades = 10000
    assert session.status()["mode"] == "paper"
    assert session.status()["live_enabled"] is False


def test_polling_runner_warmup_and_restart_backfill_end_to_end(tmp_path, monkeypatch):
    import bot

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ENABLE_WHALE", "false")
    monkeypatch.setattr(bot.signal, "signal", lambda *args: None)
    clock = [T0 + 806 * HOUR_MS + 6000]
    monkeypatch.setattr(bot.time, "time", lambda: clock[0] / 1000)
    client = Mock()

    def get_klines(**kwargs):
        if "start_time" not in kwargs:
            return [raw(candle(i)) for i in range(1, 806)]
        start = (kwargs["start_time"] - T0) // HOUR_MS
        end = (kwargs["end_time"] - T0) // HOUR_MS
        return [raw(candle(i)) for i in range(start, end + 1)]

    client.get_klines.side_effect = get_klines
    monkeypatch.setattr(bot, "CoinExClient", lambda: client)
    first = bot.run(once=True)
    assert first["last_candle_open_ms"] == T0 + 805 * HOUR_MS
    clock[0] += 2 * HOUR_MS
    second = bot.run(once=True)
    assert second["last_candle_open_ms"] == T0 + 807 * HOUR_MS
    assert second["equity"] == 10000
    assert second["live_enabled"] is False
    store = StateStore(tmp_path / "paper.sqlite3")
    assert store.load()[0] == 3
    store.close()


def test_restart_preserves_holding_period_not_just_position(tmp_path):
    store = StateStore(tmp_path / "paper.sqlite3")
    session = seeded()
    session.pending = signal(1200)
    for i in range(1200, 1206):
        session.process(candle(i), store=store)
    revision, snapshot = store.load()
    recovered = TradingSession(ZERO_COST, snapshot)
    recovered.revision = revision
    assert recovered.broker.position.bars_held == 6
    events = []
    for i in range(1206, 1211):
        events.extend(recovered.process(candle(i), store=store))
    exit_event = next(e for e in events if e["type"] == "exit")
    assert exit_event["exit_reason"] == "time-exit"
    assert exit_event["time"] == T0 + 1210 * HOUR_MS
    store.close()


def test_readonly_dashboard_volume_survives_writer_shutdown(tmp_path):
    path = tmp_path / "paper.sqlite3"
    store = StateStore(path)
    assert store.conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    store.heartbeat({"status": "stopped", "ts_ms": 1})
    store.close()
    path.chmod(0o400)
    tmp_path.chmod(0o500)
    try:
        assert read_status(path)["heartbeat"]["status"] == "stopped"
    finally:
        tmp_path.chmod(0o700)
        path.chmod(0o600)


def test_empty_checkpoint_does_not_reset_capital():
    with pytest.raises(ValueError, match="refusing to reset"):
        TradingSession(ZERO_COST, {})


def test_null_checkpoint_is_corruption_not_a_new_account(tmp_path):
    store = StateStore(tmp_path / "paper.sqlite3")
    store.conn.execute("INSERT INTO checkpoint VALUES (1,1,'null')")
    with pytest.raises(ValueError, match="refusing to reset"):
        store.load()
    store.close()
