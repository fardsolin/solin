"""Offline characterization of cb0198f, NOT an acceptance suite.
Passing observations below deliberately confirm defects in the audited snapshot.
See 2026-09-19.fa.md before interpreting results. Run this file explicitly with
pytest; it is intentionally not named test_*.py and is NOT a CI safety gate.
No exchange requests are permitted; state/logs are redirected to tmp_path.
"""
import hashlib
import hmac
import os
from pathlib import Path
import sys
from unittest.mock import Mock

os.environ['RISK_PER_TRADE_PCT'] = '0.02'
os.environ['MIN_PAPER_TRADES'] = '50'
os.environ['MAX_DAILY_LOSS_PCT'] = '0.03'
os.environ['PAPER_START_EQUITY'] = '10000'
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
import pytest
import requests
from fastapi.testclient import TestClient
import bot
import dashboard.server as dashboard
import engine.risk_manager as risk_module
from engine.broker import LiveBroker, PaperBroker
from engine.coinex_client import CoinExClient
from engine.strategy import StrategyEngine, HOLD_BARS


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    def deny_network(*args, **kwargs):
        raise AssertionError('Exchange/network requests forbidden during review')
    monkeypatch.setattr(requests.sessions.Session, 'request', deny_network)
    paths = {
        'DATA_DIR': str(tmp_path),
        'TRADE_LOG': str(tmp_path / 'trade_log.jsonl'),
        'HEARTBEAT_FILE': str(tmp_path / 'heartbeat.json'),
        'STATE_FILE': str(tmp_path / 'risk_state.json'),
        'RISK_STATE_FILE': str(tmp_path / 'risk_state.json'),
        'CONFIRM_FILE': str(tmp_path / 'CONFIRM_LIVE.txt'),
    }
    for module in [bot, risk_module, dashboard]:
        for name, value in paths.items():
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)


def candles(n=1000):
    return pd.DataFrame({
        'Time': pd.date_range('2026-01-01', periods=n, freq='h', tz='UTC'),
        'Open': 100.0, 'High': 100.2, 'Low': 99.8, 'Close': 100.0,
        'Volume': 10.0, 'TakerBuyBase': 5.0,
    })


def raw_row(ts, close=100.0, volume=10.0):
    return {
        'created_at': int(ts.timestamp() * 1000), 'open': '100',
        'high': str(max(100.2, close)), 'low': '99.8',
        'close': str(close), 'volume': str(volume),
    }


def entry(direction='long', stop=90.0):
    return {'action': 'enter', 'direction': direction, 'entry_price_ref': 100.0,
            'stop': stop, 'target': 120.0 if direction == 'long' else 80.0}


def exit_result(direction='long', price=110.0, stop=90.0):
    return {'action': 'exit', 'direction': direction, 'entry_price': 100.0,
            'exit_price': price, 'initial_stop': stop, 'reason': 'test'}


class EndRun(BaseException):
    pass


def run_mocked_polls(monkeypatch, engine, risk, responses, loops):
    client = Mock(spec=CoinExClient)
    client.get_klines.side_effect = responses
    monkeypatch.setattr(bot, 'CoinExClient', lambda: client)
    monkeypatch.setattr(bot, 'StrategyEngine', lambda: engine)
    monkeypatch.setattr(bot, 'RiskManager', lambda: risk)
    seen_heartbeats = []
    monkeypatch.setattr(bot, 'write_heartbeat', seen_heartbeats.append)
    elapsed = 0
    def sleep(_):
        nonlocal elapsed
        elapsed += 1
        if elapsed >= loops:
            raise EndRun()
    monkeypatch.setattr(bot.time, 'sleep', sleep)
    with pytest.raises(EndRun):
        bot.run()
    assert not any(h.get('status') == 'error' for h in seen_heartbeats), seen_heartbeats
    return client, seen_heartbeats


def test_control_source_compiles_without_writing_bytecode():
    root = REPO_ROOT
    sources = [root / 'bot.py', *root.glob('engine/*.py'), *root.glob('dashboard/*.py')]
    for source in sources:
        compile(source.read_text(), str(source), 'exec')
    assert len(sources) == 8


def test_control_signature_matches_documented_hmac_recipe():
    client = CoinExClient(access_id='review-key-not-real', secret_key='review-secret-not-real')
    method, path, body, ts = 'POST', '/v2/futures/order', '{"market":"BTCUSDT"}', '1700490703564'
    expected = hmac.new(b'review-secret-not-real', (method + path + body + ts).encode('latin-1'), hashlib.sha256).hexdigest()
    assert client._sign(method, path, body, ts) == expected


def test_control_live_gate_requires_count_and_explicit_confirmation():
    risk = risk_module.RiskManager()
    assert risk.current_mode() == 'paper'
    Path(risk_module.CONFIRM_FILE).write_text('I_UNDERSTAND_THE_RISK')
    assert risk.current_mode() == 'paper'
    risk.state['paper_trades_completed'] = 50
    assert risk.current_mode() == 'live'
    Path(risk_module.CONFIRM_FILE).write_text('not confirmed')
    assert risk.current_mode() == 'paper'


def test_control_daily_loss_threshold_and_reset():
    risk = risk_module.RiskManager()
    risk.record_closed_trade(-0.02)
    assert not risk.circuit_breaker_tripped()
    risk.record_closed_trade(-0.01)
    assert risk.circuit_breaker_tripped()
    risk.state['daily_date'] = '2000-01-01'
    assert not risk.circuit_breaker_tripped()
    assert risk.state['daily_pnl_pct'] == 0


def test_observe_whale_proxy_disables_both_signals_even_on_volume_spike():
    ts = pd.date_range('2026-01-01', periods=1000, freq='h', tz='UTC')
    raw = [raw_row(t, volume=10000.0 if i == 999 else 10.0) for i, t in enumerate(ts)]
    engine = StrategyEngine()
    engine.load_history(bot.klines_to_df(raw))
    d = engine._engineer()
    assert (d['Delta'] == 0).all()
    assert not d['WhaleBuy'].any()
    assert not d['WhaleSell'].any()
    print('OBSERVE whale: 1000 bars, spike=1000x, buy=0 sell=0 delta=0')


@pytest.mark.parametrize('initial_size, expected_final_action', [(800, 'exit'), (1000, 'hold')])
def test_observe_time_exit_stalls_when_rolling_buffer_is_full(initial_size, expected_final_action):
    engine = StrategyEngine()
    engine.load_history(candles(initial_size))
    engine.open_position('long', 100.0, 90.0, 120.0)
    for i in range(1, HOLD_BARS + 1):
        row = engine.candles.iloc[-1].to_dict()
        row['Time'] += pd.Timedelta(hours=1)
        engine.append_closed_candle(row)
        result = engine.check_new_bar()
    assert result['action'] == expected_final_action
    if initial_size == 1000:
        assert len(engine.candles) - 1 - engine.open_trade.entry_bar_index == 0
    else:
        assert result['reason'] == 'time-exit'
    print(f'OBSERVE time-exit: initial_size={initial_size}, elapsed_bars=10, action={result["action"]}')


def test_observe_configured_risk_is_ignored_in_sizing(monkeypatch):
    assert risk_module.RISK_PER_TRADE_PCT == 0.02
    risk = risk_module.RiskManager()
    paper = PaperBroker()
    engine = StrategyEngine()
    engine.load_history(candles(800))
    open_spy = Mock(wraps=paper.open_position)
    monkeypatch.setattr(paper, 'open_position', open_spy)
    bot.handle_result(entry(), engine, paper, risk, risk.status_report())
    actual_size = open_spy.call_args.args[2]
    assert actual_size == 10.0  # Would be 20 at the configured 2% risk.
    print('OBSERVE sizing: env risk=2%, equity=10000, stop_distance=10, expected_size=20, actual_size=10')


def test_observe_short_pnl_uses_inverse_return_formula():
    risk = risk_module.RiskManager()
    paper = PaperBroker()
    engine = StrategyEngine()
    bot.handle_result(exit_result('short', 90.0, 110.0), engine, paper, risk, risk.status_report())
    assert paper.equity == pytest.approx(10111.111111111)
    print(f'OBSERVE short PnL: linear 10 units, 100->90, expected_equity=10100, actual={paper.equity:.6f}')


def test_observe_exit_accounting_still_assumes_one_percent_risk(monkeypatch):
    risk = risk_module.RiskManager()
    risk.state['risk_per_trade_pct'] = 0.02  # Even manually fixing sizing does not fix accounting.
    paper = PaperBroker()
    engine = StrategyEngine()
    engine.load_history(candles(800))
    open_spy = Mock(wraps=paper.open_position)
    monkeypatch.setattr(paper, 'open_position', open_spy)
    bot.handle_result(entry(), engine, paper, risk, risk.status_report())
    assert open_spy.call_args.args[2] == 20
    bot.handle_result(engine._close('test', 110.0), engine, paper, risk, risk.status_report())
    assert paper.equity == 10100.0  # 20 units * 10 gain should be +200, not +100.
    print('OBSERVE accounting: actual size=20, price 100->110, expected_profit=200, recorded_profit=100')


def test_observe_close_request_omits_required_type():
    client = CoinExClient(access_id='review-key-not-real', secret_key='review-secret-not-real')
    client._request = Mock(return_value={})
    client.close_position_market('BTCUSDT')
    body = client._request.call_args.kwargs['body_obj']
    assert 'type' not in body
    print('OBSERVE close request body:', body)


def test_observe_position_request_omits_required_market_type():
    client = CoinExClient(access_id='review-key-not-real', secret_key='review-secret-not-real')
    client._request = Mock(return_value=[])
    client.get_positions('BTCUSDT')
    params = client._request.call_args.kwargs['params']
    assert 'market_type' not in params


def test_observe_documented_last_filled_price_is_ignored():
    broker = LiveBroker.__new__(LiveBroker)
    broker.client = Mock()
    broker.client.place_market_order.return_value = {
        'order_id': 1, 'last_filled_price': '101.25',
        'filled_amount': '1', 'unfilled_amount': '0',
    }
    result = broker.open_position('BTCUSDT', 'long', 1, 100.0)
    assert result['fill_price'] == 100.0
    print('OBSERVE fill: exchange last_filled_price=101.25, broker fill_price=100.0')


def test_observe_submitted_unfilled_order_becomes_local_open_trade():
    risk = risk_module.RiskManager()
    engine = StrategyEngine()
    engine.load_history(candles(800))
    broker = Mock(is_live=True)
    broker.get_equity.return_value = 10000.0
    broker.open_position.return_value = {'status': 'submitted', 'fill_price': 100.0,
                                         'order_id': 1, 'raw': {'filled_amount': '0'}}
    bot.handle_result(entry(), engine, broker, risk, risk.status_report())
    assert engine.open_trade is not None


def test_observe_failed_close_has_already_erased_local_position():
    risk = risk_module.RiskManager()
    engine = StrategyEngine()
    engine.load_history(candles(800))
    engine.open_position('long', 100.0, 90.0, 120.0)
    result = engine._manage_open_trade({'High': 100.2, 'Low': 89.0, 'Close': 95.0})
    assert result['action'] == 'exit'
    assert engine.open_trade is None
    broker = Mock(is_live=True)
    broker.close_position.side_effect = TimeoutError('simulated close timeout')
    with pytest.raises(TimeoutError):
        bot.handle_result(result, engine, broker, risk, risk.status_report())
    assert engine.open_trade is None
    assert risk.state['paper_trades_completed'] == 0
    print('OBSERVE close failure: exchange close timed out; engine.open_trade=None')


def test_observe_live_exit_increments_paper_counter_and_ignores_fill():
    risk = risk_module.RiskManager()
    broker = Mock(is_live=True)
    broker.close_position.return_value = {'status': 'submitted', 'fill_price': 80.0}
    bot.handle_result(exit_result('long', 110.0, 90.0), StrategyEngine(), broker, risk, risk.status_report())
    assert risk.state['paper_trades_completed'] == 1
    assert risk.state['daily_pnl_pct'] == pytest.approx(0.01)
    print('OBSERVE live exit: paper_count=1, reference_exit=110, reported_fill=80, recorded_pnl=+1%')


def test_observe_restart_preserves_counter_but_not_position_or_equity():
    risk = risk_module.RiskManager()
    risk.record_closed_trade(-0.01)
    paper = PaperBroker()
    paper.apply_pnl(-100.0)
    engine = StrategyEngine()
    engine.load_history(candles(800))
    engine.open_position('long', 100.0, 90.0, 120.0)
    assert risk_module.RiskManager().state['paper_trades_completed'] == 1
    assert PaperBroker().equity == 10000.0
    assert StrategyEngine().open_trade is None


def test_observe_partial_warmup_bar_is_never_replaced(monkeypatch):
    risk = risk_module.RiskManager()
    engine = StrategyEngine()
    engine.check_new_bar = Mock(return_value={'action': 'none'})
    hist_times = candles(805)['Time']
    last = hist_times.iloc[-1]
    hist = [raw_row(t, 111.0 if t == last else 100.0) for t in hist_times]
    poll1 = [raw_row(last - pd.Timedelta(hours=1)), raw_row(last, 222.0), raw_row(last + pd.Timedelta(hours=1), 333.0)]
    poll2 = [raw_row(last, 222.0), raw_row(last + pd.Timedelta(hours=1), 444.0), raw_row(last + pd.Timedelta(hours=2), 555.0)]
    client, _ = run_mocked_polls(monkeypatch, engine, risk, [hist, poll1, poll2], loops=2)
    assert client.get_klines.call_count == 3
    assert engine.check_new_bar.call_count == 1
    assert engine.candles.loc[engine.candles['Time'] == last, 'Close'].iloc[0] == 111.0
    assert engine.candles.iloc[-1]['Close'] == 444.0
    print('OBSERVE warmup: stored partial_close=111 survives final_close=222; only following candle processed')


def test_observe_gap_recovery_only_appends_latest_closed_bar(monkeypatch):
    risk = risk_module.RiskManager()
    engine = StrategyEngine()
    engine.check_new_bar = Mock(return_value={'action': 'none'})
    times = candles(805)['Time']
    last = times.iloc[-1]
    hist = [raw_row(t) for t in times]
    poll = [raw_row(last + pd.Timedelta(hours=i)) for i in [2, 3, 4]]
    run_mocked_polls(monkeypatch, engine, risk, [hist, poll], loops=1)
    assert engine.candles.iloc[-1]['Time'] - engine.candles.iloc[-2]['Time'] == pd.Timedelta(hours=3)
    assert len(engine.candles) == 806


def test_observe_breaker_skips_management_of_an_existing_trade(monkeypatch):
    risk = risk_module.RiskManager()
    risk.state['daily_pnl_pct'] = -0.03
    engine = StrategyEngine()
    engine.load_history(candles(805))
    engine.open_position('long', 100.0, 90.0, 120.0)
    engine.check_new_bar = Mock(return_value={'action': 'hold'})
    hist = [raw_row(t) for t in candles(805)['Time']]
    client, hb = run_mocked_polls(monkeypatch, engine, risk, [hist], loops=1)
    assert client.get_klines.call_count == 1  # Warmup only, no management poll.
    assert engine.check_new_bar.call_count == 0
    assert engine.open_trade is not None
    assert hb[-1]['status'] == 'circuit_breaker_tripped'


def test_observe_live_confirmation_removal_routes_live_exit_to_paper(monkeypatch):
    risk = risk_module.RiskManager()
    risk.state['paper_trades_completed'] = 50
    confirm = Path(risk_module.CONFIRM_FILE)
    confirm.write_text('I_UNDERSTAND_THE_RISK')
    assert risk.current_mode() == 'live'
    engine = StrategyEngine()
    engine.load_history(candles(805))
    engine.open_position('long', 100.0, 90.0, 120.0)  # Simulate existing live trade.
    confirm.unlink()
    assert risk.current_mode() == 'paper'
    live_factory = Mock(side_effect=AssertionError('Should not construct live broker in paper mode'))
    monkeypatch.setattr(bot, 'LiveBroker', live_factory)
    paper = PaperBroker()
    paper_close = Mock(wraps=paper.close_position)
    monkeypatch.setattr(paper, 'close_position', paper_close)
    monkeypatch.setattr(bot, 'PaperBroker', lambda **kwargs: paper)
    engine.check_new_bar = lambda: engine._close('time-exit', 100.0)
    times = candles(805)['Time']
    last = times.iloc[-1]
    hist = [raw_row(t) for t in times]
    poll = [raw_row(last + pd.Timedelta(hours=i)) for i in [0, 1, 2]]
    run_mocked_polls(monkeypatch, engine, risk, [hist, poll], loops=1)
    assert paper_close.call_count == 1
    assert live_factory.call_count == 0
    assert engine.open_trade is None
    print('OBSERVE mode switch: live trade exit sent to PaperBroker after confirmation deletion')


def test_observe_same_bar_trailing_can_report_gain_despite_initial_stop_touch():
    engine = StrategyEngine()
    engine.load_history(candles(800))
    engine.open_position('long', 100.0, 95.0, 110.0)
    result = engine._manage_open_trade({'High': 101.0, 'Low': 94.0, 'Close': 100.0})
    assert result['reason'] == 'staircase-lock'
    assert result['exit_price'] == pytest.approx(100.9)
    print('OBSERVE intrabar ambiguity: entry=100 stop=95 high=101 low=94; engine exit=100.9')


def test_observe_dashboard_reads_wrong_state_fields_and_percent_units():
    risk = risk_module.RiskManager()
    risk.record_closed_trade(-0.03)
    bot.write_heartbeat({'status': 'circuit_breaker_tripped', **risk.status_report()})
    with TestClient(dashboard.app) as client:
        response = client.get('/api/status')
        assert response.status_code == 200
        data = response.json()
        assert data['heartbeat']['circuit_breaker_tripped'] is True
        assert data['heartbeat']['daily_pnl_pct'] == -3.0
        assert data['risk_state']['daily_pnl_pct'] == -0.03
        assert 'circuit_breaker_tripped' not in data['risk_state']
        assert 'paper_trades_required' not in data['risk_state']
        html = client.get('/').text
        assert 'const s = d.risk_state || {}' in html
        assert '${s.daily_pnl_pct ?? 0}%' in html
        assert "${s.circuit_breaker_tripped ?" in html
    print('OBSERVE dashboard: heartbeat=-3%, breaker=true; displayed source=-0.03%, breaker missing')


def test_observe_fresh_error_heartbeat_is_not_stale():
    bot.write_heartbeat({'status': 'error', 'error': 'simulated market data failure'})
    with TestClient(dashboard.app) as client:
        data = client.get('/api/status').json()
    assert data['is_stale'] is False
    assert data['heartbeat']['status'] == 'error'
    assert "const staleBadge = d.is_stale ?" in dashboard.HTML_PAGE


def test_observe_truncated_heartbeat_causes_http_500():
    Path(dashboard.HEARTBEAT_FILE).write_text('{"ts":')
    with TestClient(dashboard.app, raise_server_exceptions=False) as client:
        response = client.get('/api/status')
    assert response.status_code == 500
    print('OBSERVE concurrent/truncated JSON read: /api/status returns 500')


def test_control_empty_dashboard_starts_without_exchange_access():
    with TestClient(dashboard.app) as client:
        assert client.get('/').status_code == 200
        data = client.get('/api/status').json()
    assert data['total_trades'] == 0
    assert data['is_stale'] is True
    assert data['equity_multiple'] == 1.0
