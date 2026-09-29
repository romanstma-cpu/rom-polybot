"""A busy recent history must not hide unused, still-fresh candidates."""
from datetime import datetime, timedelta, timezone

import pytest

import db


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'db_path', lambda: tmp_path / 'candidates.db')
    db.init_db()
    with db.get_db() as conn:
        yield conn


def add(conn, source, ticker, *, age=0, confidence=80, resolved=0, kind='trade_cluster'):
    created = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
    table = 'alerts' if source == 'momentum' else 'whale_trades'
    fields = 'ticker,confidence,created_at,resolved'
    values = [ticker, confidence, created, resolved]
    if source == 'momentum':
        fields += ',signal_type,direction,price'
        values += [kind, 'yes', .3]
    else:
        fields += ',trade_id,taker_side,price'
        values += [ticker, 'yes', .3]
    placeholders = ','.join('?' for _ in values)
    return conn.execute(f'INSERT INTO {table}({fields}) VALUES ({placeholders})', values).lastrowid


def fetch(conn, source, seen, limit=50):
    kwargs = dict(min_confidence=55, max_age_sec=120, seen_ids=seen, limit=limit)
    if source == 'momentum':
        return db.fetch_tradeable_momentum_signals(conn, allowed_types=['trade_cluster'], **kwargs)
    return db.fetch_tradeable_whale_signals(conn, **kwargs)


@pytest.mark.parametrize('source', ['whale', 'momentum'])
def test_unused_candidates_survive_more_than_a_page_of_traded_signals(database, source):
    wanted = {add(database, source, f'available-{i}', age=30) for i in range(50)}
    seen = {add(database, source, f'used-{i}') for i in range(200)}
    rows = fetch(database, source, seen)
    assert len(rows) == 50
    assert {row['id'] for row in rows} == wanted


@pytest.mark.parametrize('source', ['whale', 'momentum'])
def test_starvation_fix_does_not_relax_age_resolution_or_score_filters(database, source):
    wanted = add(database, source, 'eligible', age=30)
    add(database, source, 'expired', age=121)
    add(database, source, 'settled', resolved=1)
    add(database, source, 'low-score', confidence=54)
    if source == 'momentum':
        add(database, source, 'disabled-type', kind='volume_spike')
    seen = {add(database, source, f'used-{i}') for i in range(200)}
    assert [row['id'] for row in fetch(database, source, seen)] == [wanted]


@pytest.mark.parametrize('source', ['whale', 'momentum'])
def test_limit_is_exact_and_timestamps_have_a_stable_tiebreak(database, source):
    ids = [add(database, source, f'candidate-{i}') for i in range(8)]
    table = 'alerts' if source == 'momentum' else 'whale_trades'
    database.execute(f"UPDATE {table} SET created_at=datetime('now')")
    assert [row['id'] for row in fetch(database, source, {ids[-1]}, limit=3)] == ids[-4:-1][::-1]
    assert fetch(database, source, set(), limit=0) == []


def test_sources_can_reuse_one_connection_without_sharing_seen_ids(database):
    whale = add(database, 'whale', 'whale')
    momentum = add(database, 'momentum', 'momentum')
    assert fetch(database, 'whale', {whale}) == []
    assert [row['id'] for row in fetch(database, 'momentum', set())] == [momentum]
    assert [row['id'] for row in fetch(database, 'whale', set())] == [whale]


def test_large_seen_history_does_not_hit_sqlite_variable_limits(database):
    eligible = add(database, 'whale', 'unused')
    # More IDs than either common SQLite parameter cap, with no huge SQL.
    assert [row['id'] for row in fetch(database, 'whale', set(range(2, 40002)))] == [eligible]
