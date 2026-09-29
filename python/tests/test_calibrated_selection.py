"""Selection regressions; synthetic models test plumbing, not profitability."""
import asyncio
from datetime import datetime, timezone

import pytest

import db
import signal_calibration
import trader
from config import merge_with_defaults
from portfolio_replay import replay
from test_portfolio_replay import config, evidence, event, T


def signal(source='momentum'):
    return dict(id=1, ticker='TEST', price=.30, confidence=38,
                direction='yes', taker_side='yes', category='sports',
                signal_type='cluster', score_version='momentum-window-v2',
                created_at=datetime.fromtimestamp(T, timezone.utc).isoformat())


def model(sig, source='momentum', asof=T, probability=.60):
    key = signal_calibration.feature_keys(sig, source)[0][0]
    return {'asof': asof, 'bins': {key: {'lowerProbability': probability}}}


@pytest.mark.parametrize('source', ['momentum', 'whale'])
def test_qualified_edge_can_select_low_heuristic_score(source):
    sig = signal(source)
    cfg = merge_with_defaults({'allowed_momentum_signal_types': ['cluster']})
    assert not trader.should_trade(sig, source, cfg, now=T)[0]
    assert trader.should_trade(sig, source, cfg, now=T,
                              calibration=model(sig, source)) == (True, 'ok')


@pytest.mark.parametrize('asof,bins,probability', [
    (T+1, True, .60), (T-601, True, .60),
    (T, False, .60), (T, True, .31),
])
def test_future_stale_missing_or_low_edge_remains_blocked(asof, bins, probability):
    sig = signal()
    fitted = model(sig, asof=asof, probability=probability)
    if not bins:
        fitted['bins'] = {}
    cfg = merge_with_defaults({'allowed_momentum_signal_types': ['cluster']})
    assert not trader.should_trade(sig, 'momentum', cfg, now=T,
                                  calibration=fitted)[0]


@pytest.mark.parametrize('overrides', [
    {'trade_momentum': False}, {'allowed_categories': ['politics']},
    {'allowed_momentum_signal_types': []}, {'min_entry_price_cents': 40},
    {'max_entry_price_cents': 20},
])
def test_calibration_keeps_user_market_and_price_filters(overrides):
    sig = signal()
    cfg = merge_with_defaults({'allowed_momentum_signal_types': ['cluster'], **overrides})
    assert not trader.should_trade(sig, 'momentum', cfg, now=T,
                                  calibration=model(sig))[0]


def test_live_fetch_and_selection_do_not_discard_qualified_low_score(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'db_path', lambda: tmp_path / 'selection.db')
    db.init_db()
    sig = signal('whale')
    cfg = merge_with_defaults({'enable_trading': True, 'trade_momentum': False,
                              'require_qualified_edge': True})
    monkeypatch.setattr(trader, 'get_env', lambda: 'mainnet')
    monkeypatch.setattr(trader, '_is_blocked_by_daily_risk', lambda *a: (False, ''))
    monkeypatch.setattr(trader, '_lifetime_loss_tripped_main', lambda *a: (False, ''))
    monkeypatch.setattr(trader, 'can_open_new_entries', lambda *a: (True, ''))
    monkeypatch.setattr(trader, '_is_blocked_by_trading_hours', lambda *a: (False, ''))
    monkeypatch.setattr(trader, 'last_balance_read_ok', lambda *a: True)
    monkeypatch.setattr(signal_calibration, 'load_model',
                        lambda *a, **k: model(sig, 'whale', asof=trader.time.time()-1))
    calls = []

    def fetch(*a, **kw):
        assert kw['min_confidence'] == 0
        return [sig]

    async def balance(*a, **kw):
        return 100000, 0

    async def execute(candidate, *a, **kw):
        calls.append(candidate)
        return {'status': 'submitted'}

    monkeypatch.setattr(db, 'fetch_tradeable_whale_signals', fetch)
    monkeypatch.setattr(trader, 'refresh_balance', balance)
    monkeypatch.setattr(trader, 'execute_signal', execute)
    asyncio.run(trader.scan_for_trades(cfg))
    assert calls == [sig]


def test_replay_uses_same_low_score_qualified_selection(monkeypatch):
    events = evidence() + [event('settlement', {'yes_payout': 1}, 3)]
    sig = next(e['payload']['signal'] for e in events if e['kind'] == 'signal')
    sig['confidence'] = 38
    monkeypatch.setattr(signal_calibration, 'fit',
                        lambda _events, asof: model(sig, 'whale', asof, .85))
    result = replay(config(require_qualified_edge=True), events)
    assert result['submittedOrders'] > 0
    assert result['n'] == 1
