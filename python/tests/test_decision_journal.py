import asyncio
import time

import db
import decision_journal
import trader
from config import merge_with_defaults


def test_practice_cycle_records_later_stage_rejection_without_live_balance(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'db_path', lambda: tmp_path / 'decisions.db')
    db.init_db()
    cfg = merge_with_defaults({
        'enable_trading': False, 'main_paper_trading': True,
        'trade_whales': True, 'trade_momentum': False,
    })
    monkeypatch.setattr(trader, 'get_env', lambda: 'mainnet')
    monkeypatch.setattr(trader, '_is_blocked_by_trading_hours', lambda *_: (False, ''))
    monkeypatch.setattr(trader, 'should_trade', lambda *_: (True, 'ok'))
    monkeypatch.setattr(trader, '_rank_candidates', lambda *_a, **_kw: None)
    monkeypatch.setattr(db, 'fetch_tradeable_whale_signals', lambda *_a, **_kw: [
        {'id': 17, 'ticker': 'US-MARKET', 'price': 0.5, 'taker_side': 'yes'},
    ])

    async def no_live_balance(*_a, **_kw):
        raise AssertionError('Practice must not read live buying power')

    async def rejected_quote(*_a, on_skip=None, **_kw):
        on_skip('quote', 'No usable US order book')
        return None

    monkeypatch.setattr(trader, 'refresh_balance', no_live_balance)
    monkeypatch.setattr(trader, 'execute_signal', rejected_quote)
    assert asyncio.run(trader.scan_for_trades(cfg)) == []
    report = decision_journal.report()
    assert report['totals']['skipped'] == 1
    assert report['topSkipped'][0]['reason'] == 'No usable US order book'
    assert report['recent'][0]['mode'] == 'practice'
    assert report['recent'][0]['signal_id'] == 17
    assert trader.last_cycle['filterCounts'] == {'No usable US order book': 1}


def test_journal_resolves_position_status_without_calling_it_a_fill(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'db_path', lambda: tmp_path / 'positions.db')
    db.init_db()
    with db.get_db() as conn:
        position_id = db.insert_bot_position(conn, {
            'signal_source': 'whale', 'signal_id': 2, 'ticker': 'US-MARKET',
            'direction': 'yes', 'target_contracts': 1, 'limit_price_cents': 50,
            'filled_contracts': 0, 'cost_usd': 0, 'client_order_id': 'decision-test',
            'status': 'submitted', 'network': 'mainnet',
        })
    decision_journal.write([{
        'at': time.time(), 'trace_id': 'test-cycle', 'mode': 'live',
        'source': 'whale', 'ticker': 'US-MARKET', 'signal_id': 2,
        'stage': 'submission', 'outcome': 'submitted',
        'reason': 'Exchange order accepted', 'position_id': position_id,
    }])
    first = decision_journal.report()['recent'][0]
    assert first['outcome'] == 'submitted'
    assert first['positionStatus'] == 'submitted'
    with db.get_db() as conn:
        db.update_bot_position(conn, position_id, status='filled', filled_contracts=1)
    settled = decision_journal.report()['recent'][0]
    assert settled['outcome'] == 'submitted'
    assert settled['positionStatus'] == 'filled'
