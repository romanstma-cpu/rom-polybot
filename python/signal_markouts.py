"""Do a signal source's calls predict where the price goes, after costs?

Every recorded strategy signal, and every large trade on the tape as a
reference, is scored at fixed horizons from the recorded order books: the
midpoint move in the signal's direction, minus half the spread and the taker
fee at the signal's price. That is what acting at once would have cost, so a
source has to beat it to be worth following.

The replay recorder keeps only the newest order books, so each markout is
scored as soon as its horizon has passed and stored here. The report is
descriptive: it cannot approve, size or route an order.
"""
from __future__ import annotations

import json
import math
import statistics
import time

import db
import fees_us
import main_recorder

HORIZONS = (300, 3600, 4 * 3600)
VERDICT_HORIZON = 3600
TAPE_MIN_USD = 500.0
# A source needs this much before its verdict means anything. Signals in the
# same market move together, so the interval is taken across markets.
MIN_SAMPLES = 30
MIN_MARKETS = 8
LOOKBACK_SEC = 3 * 86400
BOOK_MAX_AGE_SEC = 600
MAX_SPREAD = 0.10
GRACE_SEC = 3600
RETAIN_SEC = 90 * 86400
REPORT_WINDOW_SEC = 30 * 86400

SOURCE_LABELS = {
    'whale': 'Large Trade',
    'momentum': 'Momentum',
    'tape': f'All trades over ${TAPE_MIN_USD:,.0f} (reference)',
    'tape_fade': f'Fade trades over ${TAPE_MIN_USD:,.0f} (test idea)',
}
# Test ideas are scored from the same events as the source they reverse, so
# an idea earns a practice strategy only once it beats costs here.
REVERSED = {'tape_fade': 'tape'}


def reverse(row: dict) -> dict:
    """The same markout taken the other way. Fees are symmetric in p(1-p)."""
    cost = row['half_spread_cents'] + row['fee_cents']
    return {**row, 'gross_cents': -row['gross_cents'], 'net_cents': -row['gross_cents'] - cost}

SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_markouts (
 event_id INTEGER NOT NULL, horizon_sec INTEGER NOT NULL, source TEXT NOT NULL,
 ticker TEXT NOT NULL, at REAL NOT NULL, direction INTEGER NOT NULL,
 mid0 REAL, half_spread_cents REAL, fee_cents REAL, mid_h REAL,
 gross_cents REAL, net_cents REAL,
 PRIMARY KEY(event_id, horizon_sec)
);
CREATE INDEX IF NOT EXISTS signal_markouts_at ON signal_markouts(at);
CREATE INDEX IF NOT EXISTS main_replay_ticker_kind ON main_replay_events(ticker, kind, at);
"""

_TAKER_DIRECTION = {
    ('ORDER_ACTION_BUY', 'OUTCOME_SIDE_YES'): 1,
    ('ORDER_ACTION_SELL', 'OUTCOME_SIDE_YES'): -1,
    ('ORDER_ACTION_BUY', 'OUTCOME_SIDE_NO'): -1,
    ('ORDER_ACTION_SELL', 'OUTCOME_SIDE_NO'): 1,
}


def init() -> None:
    main_recorder.init()
    with db.get_db() as conn:
        conn.executescript(SCHEMA)


def _price(level) -> float | None:
    try:
        value = float(level['px']['value'])
    except (KeyError, TypeError, ValueError):
        return None
    return value if math.isfinite(value) and 0 < value < 1 else None


def book_mid(payload: dict) -> tuple[float, float] | None:
    """Midpoint and half-spread (both as fractions of $1) of a recorded book."""
    bids = [p for p in map(_price, payload.get('bids') or []) if p is not None]
    offers = [p for p in map(_price, payload.get('offers') or []) if p is not None]
    if not bids or not offers:
        return None
    bid, ask = max(bids), min(offers)
    if ask <= bid or ask - bid > MAX_SPREAD:
        return None
    return (bid + ask) / 2, (ask - bid) / 2


def event_direction(kind: str, payload: dict) -> tuple[str, int] | None:
    """The source and direction (+1 buys YES, -1 buys NO) an event calls for."""
    if kind == 'signal':
        source = payload.get('source')
        row = payload.get('signal') or {}
        if source == 'whale':
            side = (row.get('taker_side') or '').lower()
        elif source == 'momentum':
            side = (row.get('direction') or '').lower()
        else:
            return None
        if side not in ('yes', 'no'):
            return None
        return source, 1 if side == 'yes' else -1
    if kind == 'trade':
        trade = payload.get('trade') or {}
        taker = trade.get('taker') or {}
        direction = _TAKER_DIRECTION.get((taker.get('action'), taker.get('outcomeSide')))
        if direction is None:
            return None
        try:
            price = float(trade['price']['value'])
            quantity = float(trade['quantity']['value'])
        except (KeyError, TypeError, ValueError):
            return None
        side_price = price if taker.get('outcomeSide') == 'OUTCOME_SIDE_YES' else 1 - price
        if not math.isfinite(quantity * side_price) or quantity * side_price < TAPE_MIN_USD:
            return None
        return 'tape', direction
    return None


def _mid_near(conn, ticker: str, at: float, max_age: float) -> tuple[float, float] | None:
    rows = conn.execute(
        "SELECT payload FROM main_replay_events WHERE ticker=? AND kind='book' "
        'AND at<=? AND at>=? ORDER BY at DESC LIMIT 5',
        (ticker, at, at - max_age),
    ).fetchall()
    for row in rows:
        try:
            mid = book_mid(json.loads(row['payload']))
        except (TypeError, ValueError):
            continue
        if mid is not None:
            return mid
    return None


def score(direction: int, at: float, start: tuple[float, float], end_mid: float) -> dict:
    """Markout in cents: gross midpoint move, and net of spread and taker fee."""
    mid0, half_spread = start
    side_price = mid0 if direction > 0 else 1 - mid0
    fee = float(fees_us.coefficient_at_or_earliest(at)) * side_price * (1 - side_price) * 100
    gross = direction * (end_mid - mid0) * 100
    return {
        'mid0': mid0, 'half_spread_cents': half_spread * 100, 'fee_cents': fee,
        'mid_h': end_mid, 'gross_cents': gross, 'net_cents': gross - half_spread * 100 - fee,
    }


def collect(now: float | None = None, *, limit: int = 2000) -> int:
    """Score every markout whose horizon has passed. Returns rows stored."""
    init()
    now = time.time() if now is None else now
    stored = 0
    with db.get_db() as conn:
        events = conn.execute(
            "SELECT e.id, e.at, e.kind, e.ticker, e.payload FROM main_replay_events e "
            "WHERE e.kind IN ('signal','trade') AND e.ticker<>'' AND e.at>=? AND e.at<=? "
            'AND (SELECT COUNT(*) FROM signal_markouts m WHERE m.event_id=e.id)<? '
            'ORDER BY e.at LIMIT ?',
            (now - LOOKBACK_SEC, now - min(HORIZONS), len(HORIZONS), limit),
        ).fetchall()
        for event in events:
            try:
                called = event_direction(event['kind'], json.loads(event['payload']))
            except (TypeError, ValueError):
                called = None
            if called is None:
                # Mark small or unreadable trades once so they are not re-read.
                conn.executemany(
                    'INSERT OR IGNORE INTO signal_markouts(event_id,horizon_sec,source,ticker,at,direction) '
                    "VALUES (?,?,'skip',?,?,0)",
                    [(event['id'], h, event['ticker'], event['at']) for h in HORIZONS],
                )
                continue
            source, direction = called
            done = {r[0] for r in conn.execute(
                'SELECT horizon_sec FROM signal_markouts WHERE event_id=?', (event['id'],))}
            start = _mid_near(conn, event['ticker'], event['at'], BOOK_MAX_AGE_SEC)
            for horizon in HORIZONS:
                due_at = event['at'] + horizon
                if horizon in done or due_at > now:
                    continue
                end = None
                if start is not None:
                    # The end price must come from a book recorded after the
                    # signal, never the one it was priced from.
                    age = min(horizon - 0.001, max(BOOK_MAX_AGE_SEC, horizon / 4))
                    end = _mid_near(conn, event['ticker'], due_at, age)
                if end is None and start is not None and now < due_at + GRACE_SEC:
                    continue  # the book for this horizon may still be on its way
                values = score(direction, event['at'], start, end[0]) if start and end else {}
                conn.execute(
                    'INSERT OR IGNORE INTO signal_markouts(event_id,horizon_sec,source,ticker,at,direction,'
                    'mid0,half_spread_cents,fee_cents,mid_h,gross_cents,net_cents) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    (event['id'], horizon, source, event['ticker'], event['at'], direction,
                     values.get('mid0'), values.get('half_spread_cents'), values.get('fee_cents'),
                     values.get('mid_h'), values.get('gross_cents'), values.get('net_cents')),
                )
                stored += 1
        conn.execute('DELETE FROM signal_markouts WHERE at<?', (now - RETAIN_SEC,))
    return stored


def summarize(rows: list[dict]) -> dict:
    """Mean markouts with a 95% interval taken across markets, not signals."""
    by_market: dict[str, list[float]] = {}
    for row in rows:
        by_market.setdefault(row['ticker'], []).append(row['net_cents'])
    market_means = [statistics.fmean(v) for v in by_market.values()]
    out = {
        'samples': len(rows),
        'markets': len(by_market),
        'grossCents': statistics.fmean(r['gross_cents'] for r in rows) if rows else None,
        'costCents': statistics.fmean(r['half_spread_cents'] + r['fee_cents'] for r in rows) if rows else None,
        'netCents': statistics.fmean(market_means) if market_means else None,
        'ciLowCents': None,
        'ciHighCents': None,
    }
    if len(market_means) >= 2:
        half = 1.96 * statistics.stdev(market_means) / math.sqrt(len(market_means))
        out['ciLowCents'] = out['netCents'] - half
        out['ciHighCents'] = out['netCents'] + half
    return out


def verdict(summary: dict) -> tuple[str, str]:
    n, markets = summary['samples'], summary['markets']
    if n < MIN_SAMPLES or markets < MIN_MARKETS or summary['ciLowCents'] is None:
        return 'collecting', (
            f'{n} of {MIN_SAMPLES} scored signals across {markets} of {MIN_MARKETS} markets. '
            'Too few to judge yet.')
    low, high = summary['ciLowCents'], summary['ciHighCents']
    if low > 0:
        return 'predictive', f'Prices moved its way by more than costs ({low:+.2f}c to {high:+.2f}c per contract).'
    if high < 0:
        return 'negative', f'Following it lost money after costs ({low:+.2f}c to {high:+.2f}c per contract).'
    return 'no_edge', f'No edge after costs shown yet ({low:+.2f}c to {high:+.2f}c per contract).'


def report(now: float | None = None) -> dict:
    init()
    now = time.time() if now is None else now
    with db.get_db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT source,ticker,horizon_sec,gross_cents,net_cents,half_spread_cents,fee_cents "
            "FROM signal_markouts WHERE source<>'skip' AND net_cents IS NOT NULL AND at>=?",
            (now - REPORT_WINDOW_SEC,),
        )]
    sources = []
    for source, label in SOURCE_LABELS.items():
        if source in REVERSED:
            mine = [reverse(r) for r in rows if r['source'] == REVERSED[source]]
        else:
            mine = [r for r in rows if r['source'] == source]
        horizons = [{'horizonSec': h, **summarize([r for r in mine if r['horizon_sec'] == h])}
                    for h in HORIZONS]
        status, reason = verdict(next(h for h in horizons if h['horizonSec'] == VERDICT_HORIZON))
        sources.append({'source': source, 'label': label, 'reference': source in ('tape', 'tape_fade'),
                        'status': status, 'reason': reason, 'horizons': horizons})
    return {
        'asOf': now,
        'windowDays': REPORT_WINDOW_SEC // 86400,
        'verdictHorizonSec': VERDICT_HORIZON,
        'minSamples': MIN_SAMPLES,
        'minMarkets': MIN_MARKETS,
        'controlsLiveTrading': False,
        'sources': sources,
    }
