"""Signal markouts: direction, costs, scoring as books arrive, and verdicts."""
from __future__ import annotations

import json

import pytest

import db
import fees_us
import main_recorder
import signal_markouts as sm

T0 = 1_790_000_000.0  # after the first published US fee schedule


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    dbfile = tmp_path / 'markouts.db'
    monkeypatch.setattr(db, 'db_path', lambda: dbfile)
    db.init_db()
    sm.init()
    return dbfile


def _insert(kind, ticker, payload, at):
    with db.get_db() as conn:
        cur = conn.execute('INSERT INTO main_replay_events(at,kind,ticker,payload) VALUES (?,?,?,?)',
                           (at, kind, ticker, json.dumps(payload)))
        return cur.lastrowid


def _book(ticker, bid, ask, at):
    _insert('book', ticker, {'bids': [{'px': {'value': f'{bid:.4f}'}, 'qty': '100'}],
                             'offers': [{'px': {'value': f'{ask:.4f}'}, 'qty': '100'}]}, at)


def _trade(ticker, *, price, qty, action='ORDER_ACTION_BUY', side='OUTCOME_SIDE_YES', at=T0):
    return _insert('trade', ticker, {'trade': {
        'price': {'value': str(price)}, 'quantity': {'value': str(qty)},
        'taker': {'action': action, 'outcomeSide': side}}}, at)


def _signal(ticker, source, side, at=T0):
    key = 'taker_side' if source == 'whale' else 'direction'
    return _insert('signal', ticker, {'source': source, 'signal': {key: side}}, at)


def _rows():
    with db.get_db() as conn:
        return [dict(r) for r in conn.execute(
            'SELECT * FROM signal_markouts ORDER BY event_id, horizon_sec')]


def test_direction_of_signals_and_taker_trades():
    assert sm.event_direction('signal', {'source': 'whale', 'signal': {'taker_side': 'no'}}) == ('whale', -1)
    assert sm.event_direction('signal', {'source': 'momentum', 'signal': {'direction': 'yes'}}) == ('momentum', 1)
    assert sm.event_direction('signal', {'source': 'convergence', 'signal': {}}) is None
    big = {'trade': {'price': {'value': '0.40'}, 'quantity': {'value': '2000'},
                     'taker': {'action': 'ORDER_ACTION_SELL', 'outcomeSide': 'OUTCOME_SIDE_YES'}}}
    assert sm.event_direction('trade', big) == ('tape', -1)  # selling YES calls for NO
    # Buying NO at a YES price of 0.40 costs 0.60 a contract: notional is $1,200.
    big['trade']['taker'] = {'action': 'ORDER_ACTION_BUY', 'outcomeSide': 'OUTCOME_SIDE_NO'}
    assert sm.event_direction('trade', big) == ('tape', -1)
    small = {'trade': {**big['trade'], 'quantity': {'value': '10'}}}
    assert sm.event_direction('trade', small) is None


def test_book_mid_rejects_one_sided_crossed_and_wide_books():
    level = lambda p: {'px': {'value': str(p)}}
    assert sm.book_mid({'bids': [level(0.40)], 'offers': [level(0.44)]}) == pytest.approx((0.42, 0.02))
    assert sm.book_mid({'bids': [level(0.40)], 'offers': []}) is None
    assert sm.book_mid({'bids': [level(0.45)], 'offers': [level(0.44)]}) is None
    assert sm.book_mid({'bids': [level(0.20)], 'offers': [level(0.40)]}) is None


def test_score_charges_half_spread_and_taker_fee():
    out = sm.score(-1, T0, (0.40, 0.01), 0.35)
    fee = float(fees_us.coefficient(T0)) * 0.60 * 0.40 * 100
    assert out['gross_cents'] == pytest.approx(5.0)
    assert out['fee_cents'] == pytest.approx(fee)
    assert out['net_cents'] == pytest.approx(5.0 - 1.0 - fee)


def test_collect_scores_due_horizons_and_waits_for_late_books(fresh_db):
    _book('M1', 0.48, 0.50, T0 - 5)
    sid = _signal('M1', 'whale', 'yes')
    _book('M1', 0.52, 0.54, T0 + 300)
    # 10 minutes in, only the 5-minute markout is due.
    assert sm.collect(T0 + 600) == 1
    first = _rows()
    assert [(r['event_id'], r['horizon_sec'], r['source']) for r in first] == [(sid, 300, 'whale')]
    assert first[0]['gross_cents'] == pytest.approx(4.0)
    # The 1-hour book has not been recorded yet: keep waiting within the grace period.
    assert sm.collect(T0 + 3600 + 60) == 0
    _book('M1', 0.55, 0.57, T0 + 3590)
    assert sm.collect(T0 + 3600 + 120) == 1
    assert _rows()[1]['gross_cents'] == pytest.approx(7.0)


def test_collect_gives_up_after_grace_and_marks_small_trades_once(fresh_db):
    _book('M2', 0.30, 0.32, T0 - 5)
    sid = _signal('M2', 'momentum', 'no')
    tid = _trade('M2', price=0.31, qty=5)
    now = T0 + 4 * 3600 + sm.GRACE_SEC + 1
    sm.collect(now)
    rows = _rows()
    signal_rows = [r for r in rows if r['event_id'] == sid]
    assert len(signal_rows) == 3 and all(r['net_cents'] is None for r in signal_rows)
    skipped = [r for r in rows if r['event_id'] == tid]
    assert len(skipped) == 3 and {r['source'] for r in skipped} == {'skip'}
    assert sm.collect(now + 60) == 0


def test_report_waits_for_enough_markets_then_judges_net_of_costs(fresh_db):
    # 10 markets, 3 signals each; every whale call is followed by a 6c move.
    for m in range(10):
        ticker = f'W{m}'
        for k in range(3):
            at = T0 + m * 50_000 + k * 10_000
            _book(ticker, 0.49, 0.51, at - 1)
            _signal(ticker, 'whale', 'yes', at=at)
            for h in sm.HORIZONS:
                _book(ticker, 0.55 + 0.001 * k, 0.57 + 0.001 * k, at + h - 1)
            sm.collect(at + 4 * 3600 + 10)
    rep = sm.report(T0 + 600_000)
    whale = next(s for s in rep['sources'] if s['source'] == 'whale')
    assert whale['status'] == 'predictive'
    hour = next(h for h in whale['horizons'] if h['horizonSec'] == 3600)
    assert hour['samples'] == 30 and hour['markets'] == 10
    assert hour['grossCents'] == pytest.approx(6.1, abs=0.2)
    assert hour['netCents'] < hour['grossCents']
    momentum = next(s for s in rep['sources'] if s['source'] == 'momentum')
    assert momentum['status'] == 'collecting'
    assert rep['controlsLiveTrading'] is False


def test_verdicts():
    base = {'samples': 40, 'markets': 12, 'netCents': 0.0}
    assert sm.verdict({**base, 'ciLowCents': 0.2, 'ciHighCents': 1.0})[0] == 'predictive'
    assert sm.verdict({**base, 'ciLowCents': -1.0, 'ciHighCents': -0.1})[0] == 'negative'
    assert sm.verdict({**base, 'ciLowCents': -0.5, 'ciHighCents': 0.4})[0] == 'no_edge'
    assert sm.verdict({**base, 'markets': 3, 'ciLowCents': 0.2, 'ciHighCents': 1.0})[0] == 'collecting'
