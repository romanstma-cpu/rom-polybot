"""Live crypto entries need recorded evidence that the strategy earns money.

The main engine will not trade a signal group live until settled, held-out
outcomes show a net edge after costs. The crypto engine had no such rule:
switched on, it placed real orders for whatever its settings said, whether or
not those settings had ever made money. Its default buys favorites at 95-98c,
where a strategy can win most windows and still lose, because each miss costs
what twenty or thirty wins earn.

The recorder already logs every window's book and outcome whether or not the
engine trades, so the evidence exists. This module replays the engine's own
entry rule over the last EVIDENCE_DAYS of it (the same simulation as the
Crypto tab's backtest, one contract per window, plus a cent of slippage,
each fill paying the worse of the ask at its tick and the tick before) and
qualifies the configuration only when all of these hold:

  * at least MIN_TRADES replayed trades over at least MIN_DAYS UTC days;
  * expected profit stays positive with the loss rate at the upper end of its
    95% confidence range. A sample with few or no losses says little about
    how rare losses are, and for near-certain entries that is the whole
    question; a plain bootstrap cannot see losses that did not happen;
  * the 5th percentile of a day-block bootstrap of profit per trade is
    positive;
  * both chronological halves made money, so an old winning run cannot hide a
    recent decline.

Nothing here places orders. The replay runs off the event loop and its
verdict is cached per configuration; entries wait while it is first computed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import random
import time
from typing import Optional

import crypto15m

logger = logging.getLogger("crypto15m")

EVIDENCE_DAYS = 14
MIN_TRADES = 50
MIN_DAYS = 5
SLIPPAGE_USD = 0.01          # per contract, on every replayed fill
REFRESH_SEC = 1800.0
RETRY_FAILED_SEC = 300.0
BOOTSTRAP_ROUNDS = 600
LOSS_RATE_Z = 1.645          # one-sided 95%
MIN_ASSET_TRADES = 20        # below this a coin's own record decides nothing

# Settings that size, pace or cap the engine but do not change which trades
# it would take. Changing them must not throw away a verdict.
_NOT_STRATEGY = frozenset({
    "crypto15m_enabled", "crypto15m_order_size", "crypto15m_sizing_mode",
    "crypto15m_kelly_fraction",
    "crypto15m_balance_pct", "crypto15m_max_loss_pct",
    "crypto15m_max_concurrent", "crypto15m_daily_loss_limit",
    "crypto15m_lifetime_loss_limit_pct", "crypto15m_lifetime_loss_limit_usd",
    "crypto15m_take_profit_total", "crypto15m_poll_sec",
    "crypto15m_record_signals", "crypto15m_streak_sizing",
    "crypto15m_streak_loss_pct", "crypto15m_streak_win_pct",
    "crypto15m_streak_max_mult", "crypto15m_autosize_to_min_notional",
    "crypto15m_model_autopause", "crypto15m_require_proven_edge",
    "crypto15m_spot_ws", "crypto15m_rtds_ws",
})

_verdicts: dict[tuple[str, str], dict] = {}
_refreshing: dict[tuple[str, str], asyncio.Task] = {}


def required(cfg: dict) -> bool:
    return bool(cfg.get("crypto15m_require_proven_edge", True))


def fingerprint(cfg: dict) -> str:
    strategy = {k: v for k, v in sorted(cfg.items())
                if k.startswith("crypto15m_") and k not in _NOT_STRATEGY}
    blob = json.dumps(strategy, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _wilson_ub(k: int, n: int, z: float) -> float:
    if n <= 0:
        return 1.0
    p = k / n
    denom = 1.0 + z * z / n
    center = p + z * z / (2 * n)
    rad = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return min(1.0, (center + rad) / denom)


def assess(trades: list[dict], *, since_days: int = EVIDENCE_DAYS) -> dict:
    """Qualify a replayed trade list. Pure: no database, no clock."""
    rows = sorted(
        ({"at": str(t.get("at") or ""),
          "pnl": float(t["pnlUsd"]) - SLIPPAGE_USD,
          "cost": float(t.get("costCents") or 0.0) / 100.0,
          "asset": str(t.get("asset") or "")}
         for t in trades),
        key=lambda r: r["at"])
    n = len(rows)
    days = sorted({r["at"][:10] for r in rows if r["at"]})
    out = {
        "qualified": False, "n": n, "days": len(days),
        "sinceDays": since_days, "minTrades": MIN_TRADES, "minDays": MIN_DAYS,
        "winRate": None, "evCents": None, "conservativeEvCents": None,
        "lowerEvCents": None, "halvesPositive": None, "reason": "",
        "lossRateHi": None, "meanWinCents": None, "meanLossCents": None,
        "kellyFraction": 0.0, "byAsset": {}, "excludedAssets": [],
    }
    if n:
        out["winRate"] = round(sum(1 for r in rows if r["pnl"] > 0) / n, 4)
        out["evCents"] = round(100.0 * sum(r["pnl"] for r in rows) / n, 2)
    if n < MIN_TRADES or len(days) < MIN_DAYS:
        out["reason"] = (
            f"not enough evidence yet: {n} of {MIN_TRADES} replayed trades over "
            f"{len(days)} of {MIN_DAYS} days in the last {since_days} days")
        return out

    wins = [r["pnl"] for r in rows if r["pnl"] > 0]
    losses = [-r["pnl"] for r in rows if r["pnl"] <= 0]
    mean_win = sum(wins) / len(wins) if wins else 0.0
    # With no loss observed, a loss still costs what the contract cost.
    mean_loss = (sum(losses) / len(losses) if losses
                 else sum(r["cost"] for r in rows) / n + SLIPPAGE_USD)
    loss_rate_hi = _wilson_ub(len(losses), n, LOSS_RATE_Z)
    conservative = (1.0 - loss_rate_hi) * mean_win - loss_rate_hi * mean_loss
    out["conservativeEvCents"] = round(100.0 * conservative, 2)
    out["lossRateHi"] = round(loss_rate_hi, 4)
    out["meanWinCents"] = round(100.0 * mean_win, 2)
    out["meanLossCents"] = round(100.0 * mean_loss, 2)
    stake = sum(r["cost"] for r in rows) / n + SLIPPAGE_USD
    out["kellyFraction"] = round(kelly_fraction(
        1.0 - loss_rate_hi, mean_win, mean_loss, stake), 4)
    out["byAsset"], out["excludedAssets"] = _asset_breakdown(rows)

    by_day: dict[str, list[float]] = {}
    for r in rows:
        by_day.setdefault(r["at"][:10], []).append(r["pnl"])
    blocks = [(sum(v), len(v)) for v in by_day.values()]
    rng = random.Random(314159)
    means = []
    for _ in range(BOOTSTRAP_ROUNDS):
        picked = rng.choices(blocks, k=len(blocks))
        means.append(sum(p for p, _ in picked) / sum(c for _, c in picked))
    means.sort()
    lower = means[int(0.05 * BOOTSTRAP_ROUNDS)]
    out["lowerEvCents"] = round(100.0 * lower, 2)

    half = n // 2
    halves = (sum(r["pnl"] for r in rows[:half]) > 0,
              sum(r["pnl"] for r in rows[half:]) > 0)
    out["halvesPositive"] = all(halves)

    if conservative <= 0:
        out["reason"] = (
            f"not proven: won {out['winRate']:.1%} for {out['evCents']:+.2f}c per "
            f"contract, but with the loss rate at the top of its likely range "
            f"({loss_rate_hi:.1%}) each trade would lose "
            f"{-out['conservativeEvCents']:.2f}c")
    elif lower <= 0:
        out["reason"] = (
            f"not proven: {out['evCents']:+.2f}c per contract on average, but "
            f"the day-to-day spread puts the low end at {out['lowerEvCents']:+.2f}c")
    elif not out["halvesPositive"]:
        out["reason"] = ("not proven: one half of the period lost money "
                         f"({'earlier' if not halves[0] else 'later'} half)")
    else:
        out["qualified"] = True
        out["reason"] = (
            f"proven on {n} replayed trades over {len(days)} days: "
            f"{out['evCents']:+.2f}c per contract, low end {out['lowerEvCents']:+.2f}c")
    return out


def kelly_fraction(p_win: float, mean_win: float, mean_loss: float,
                   stake: float) -> float:
    """Growth-optimal share of bankroll to stake on one trade, or 0.

    A trade that stakes `stake` per contract and wins `mean_win` or loses
    `mean_loss` per contract, with probability `p_win` of winning, has the
    Kelly optimum stake * (p / loss - q / win). Held to settlement at cost c
    this reduces to (p - c) / (1 - c). Fed the pessimistic loss rate, it is
    zero exactly when the evidence gate's conservative profit is.
    """
    if mean_win <= 0 or mean_loss <= 0 or stake <= 0:
        return 0.0
    f = stake * (p_win / mean_loss - (1.0 - p_win) / mean_win)
    return max(0.0, min(1.0, f))


def _asset_breakdown(rows: list[dict]) -> tuple[dict, list[str]]:
    """Per-coin record, and the coins that clearly lose.

    A coin is excluded only when it has MIN_ASSET_TRADES replayed trades and
    even the optimistic end (95th percentile of a bootstrap) of its profit
    per trade is below zero. Unproven is not the same as losing: a coin with
    too few trades, or a mixed record, stays in.
    """
    by: dict[str, list[float]] = {}
    for r in rows:
        if r["asset"]:
            by.setdefault(r["asset"], []).append(r["pnl"])
    out: dict[str, dict] = {}
    excluded: list[str] = []
    for asset, pnls in sorted(by.items()):
        n = len(pnls)
        row = {"n": n, "evCents": round(100.0 * sum(pnls) / n, 2),
               "upperEvCents": None, "excluded": False}
        if n >= MIN_ASSET_TRADES:
            rng = random.Random(271828)
            means = sorted(sum(rng.choices(pnls, k=n)) / n
                           for _ in range(BOOTSTRAP_ROUNDS))
            upper = means[int(0.95 * BOOTSTRAP_ROUNDS)]
            row["upperEvCents"] = round(100.0 * upper, 2)
            if upper < 0:
                row["excluded"] = True
                excluded.append(asset)
        out[asset] = row
    return out, excluded


def evaluate(cfg: dict, env: str, *, since_days: int = EVIDENCE_DAYS) -> dict:
    """Replay the configured strategy over recorded windows and assess it."""
    import replay
    run = dict(cfg)
    run["crypto15m_enabled"] = True
    # The replay scores the strategy, not the live pause that sits on top.
    run["crypto15m_model_autopause"] = False
    by_window = replay.load_windows(
        env=env, interval=crypto15m._interval(run), since_days=since_days)
    trades, _windows, _misses = replay._simulate(
        by_window, run, contracts=1, pessimistic=True)
    verdict = assess(trades, since_days=since_days)
    verdict["windows"] = len(by_window)
    return verdict


def verdict(cfg: dict, env: str) -> Optional[dict]:
    """The latest verdict for this configuration, fresh or not, if any."""
    return _verdicts.get((env, fingerprint(cfg)))


def ensure_fresh(cfg: dict, env: str) -> None:
    """Start a background replay when this configuration's verdict is stale."""
    key = (env, fingerprint(cfg))
    have = _verdicts.get(key)
    if have and time.time() - have.get("evaluatedAt", 0.0) < REFRESH_SEC:
        return
    running = _refreshing.get(key)
    if running and not running.done():
        return
    snapshot = dict(cfg)

    async def _run() -> None:
        try:
            result = await asyncio.to_thread(evaluate, snapshot, env)
            result["evaluatedAt"] = time.time()
        except Exception as e:  # noqa: BLE001 - a failed replay is not proof
            logger.warning(f"[crypto15m] evidence replay failed: {e}")
            result = {"qualified": False, "reason": (
                f"the evidence replay failed ({type(e).__name__}); retrying "
                "in a few minutes")}
            # Stale sooner than a real verdict, so the retry comes early.
            result["evaluatedAt"] = time.time() - REFRESH_SEC + RETRY_FAILED_SEC
        _verdicts[key] = result
        if len(_verdicts) > 32:
            oldest = min(_verdicts, key=lambda k: _verdicts[k].get("evaluatedAt", 0.0))
            _verdicts.pop(oldest, None)
        for done in [k for k, t in _refreshing.items() if t.done() and k != key]:
            _refreshing.pop(done, None)

    _refreshing[key] = asyncio.get_running_loop().create_task(_run())


def gate(cfg: dict, env: str, asset: str = "") -> tuple[bool, str]:
    """May a live entry go ahead under this configuration, on this coin?"""
    if not required(cfg):
        return True, ""
    have = verdict(cfg, env)
    if have is None:
        return False, "no proven edge yet: checking your recorded windows"
    if not have.get("qualified"):
        return False, f"no proven edge ({have.get('reason') or 'not qualified'})"
    if asset and asset in (have.get("excludedAssets") or []):
        rec = (have.get("byAsset") or {}).get(asset) or {}
        return False, (
            f"{asset} left out: it lost money in the replay "
            f"({rec.get('evCents', 0):+.2f}c per contract over {rec.get('n', 0)} "
            f"trades; even the optimistic end is {rec.get('upperEvCents', 0):+.2f}c)")
    return True, ""
