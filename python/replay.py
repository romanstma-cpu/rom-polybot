from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Optional

import backtest as bt
import crypto15m
import crypto15m_evidence
import crypto15m_trader
import db as dbmod

_DERIVABLE = {
    "hasMarket", "favorite", "favoritePrice", "entryCost", "minsLeft",
    "inWindow", "signal", "modelProb", "edgeNetCents", "spotLive",
    "upAsk", "downAsk", "yesBid", "yesAsk", "upProb", "downProb",
    "deltaPct", "deltaSignedPct", "sigma1m", "spotUsd", "strikeUsd",
    "macd", "macdSignal", "macdHist", "macdCross", "rsi", "hourUtc",
    "closeTime", "ticker", "asset", "series",
    "vwap1h", "ema12", "sma20", "sma50", "priceVsVwapPct",
    "ema12VsSma20Pct", "ema1VsSma5Pct", "velocity1mPct",
    "change5mPct", "change15mPct",
    "spreadCents", "modelEdgePts", "favoriteAskCents", "timeFracLeft",
}


def tick_to_asset(row: dict, cfg: dict, close_iso: str) -> dict:
    up_prob = row.get("up_prob")
    yes_bid, yes_ask = row.get("yes_bid"), row.get("yes_ask")
    no_ask = row.get("no_ask")
    fav = None
    fav_price = None
    if up_prob is not None:
        fav = "up" if float(up_prob) >= 0.5 else "down"
        fav_price = float(up_prob) if fav == "up" else 1.0 - float(up_prob)
    ml = row.get("mins_left")
    in_window = (
        ml is not None
        and float(ml) <= float(crypto15m._const(cfg, "time_delay_min"))
    )
    hour = None
    ts = row.get("observed_at") or ""
    try:
        hour = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").hour
    except Exception:
        pass
    entry_cost = None
    if fav == "up":
        entry_cost = yes_ask
    elif fav == "down":
        entry_cost = no_ask
    if entry_cost is None and fav_price is not None:
        entry_cost = fav_price
    dp = row.get("delta_pct")
    signal = bool(
        in_window
        and fav_price is not None
        and fav_price >= crypto15m._const(cfg, "entry_threshold")
        and (entry_cost is None or float(entry_cost) <= crypto15m._const(cfg, "entry_max"))
        and (dp is None or float(dp) >= crypto15m._const(cfg, "min_delta_pct"))
        and (hour is None or crypto15m.hours_ok(cfg, hour=hour))
    )
    src = str(row.get("spot_source") or "")
    up_ask = row.get("up_ask")
    if up_ask is None and fav == "up" and row.get("ws_ask") is not None:
        up_ask = yes_ask
    model_prob, edge_net = _reprice_model(row, cfg, ml, up_ask, no_ask)
    out = {
        "asset": row.get("asset"), "ticker": row.get("ticker"),
        "series": f"{row.get('asset')}-updown", "hasMarket": True,
        "closeTime": close_iso,
        "favorite": fav, "favoritePrice": fav_price, "entryCost": entry_cost,
        "minsLeft": float(ml) if ml is not None else None,
        "inWindow": in_window, "signal": signal, "hourUtc": hour,
        "modelProb": model_prob,
        "edgeNetCents": edge_net,
        "spotLive": ("rtds-ws" in src) or ("coinbase-ws" in src),
        "upAsk": up_ask, "downAsk": no_ask,
        "yesBid": yes_bid, "yesAsk": yes_ask,
        "upProb": up_prob,
        "downProb": (1.0 - float(up_prob)) if up_prob is not None else None,
        "deltaPct": row.get("delta_pct"),
        "deltaSignedPct": row.get("delta_signed_pct"),
        "sigma1m": row.get("sigma1m"),
        "spotUsd": row.get("spot"), "strikeUsd": row.get("strike"),
        "macd": row.get("macd"), "macdSignal": row.get("macd_signal"),
        "macdHist": row.get("macd_hist"), "macdCross": row.get("macd_cross"),
        "rsi": row.get("rsi"),
        "vwap1h": row.get("vwap1h"), "ema12": row.get("ema12"),
        "sma20": row.get("sma20"), "sma50": row.get("sma50"),
        "priceVsVwapPct": row.get("price_vs_vwap_pct"),
        "ema12VsSma20Pct": row.get("ema12_vs_sma20_pct"),
        "ema1VsSma5Pct": row.get("ema1_vs_sma5_pct"),
        "velocity1mPct": row.get("velocity1m_pct"),
        "change5mPct": row.get("change5m_pct"),
        "change15mPct": row.get("change15m_pct"),
        # Lets the fee model charge the schedule in force at this tick
        # rather than today's. See crypto15m.asset_fee_at.
        "observedAt": row.get("observed_at"),
    }
    crypto15m.derive_script_fields(out, crypto15m._interval(cfg))
    return out


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _reprice_model(row: dict, cfg: dict, mins_left, up_ask, no_ask):
    """The model probability and edge this config would have seen.

    A tick stores the probability of whichever model was live when it was
    recorded. Replaying that number would score every tail setting on the old
    model's calls, so the tick is re-priced from its recorded spot, strike,
    volatility and time left. A tick missing any of those keeps its recorded
    values.
    """
    strike = row.get("strike") if row.get("strike") is not None else row.get("open_spot")
    prob = crypto15m.model_up_prob(
        _num(row.get("spot")), _num(strike), _num(row.get("sigma1m")),
        _num(mins_left), crypto15m.model_tail_dof(cfg))
    if prob is None:
        return row.get("model_prob"), row.get("edge_net_cents")
    at = crypto15m.asset_fee_at({"observedAt": row.get("observed_at")})
    return prob, crypto15m.model_edge_net_cents(
        prob, _num(up_ask), _num(no_ask), None, at)


def _tick_epoch(row: dict) -> Optional[float]:
    ts = str(row.get("observed_at") or "")
    try:
        return datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return None


def _exec_ask(row: dict, side: str) -> Optional[float]:
    if side == "down":
        v = row.get("no_ask")
    else:
        v = row.get("up_ask")
        if v is None and row.get("ws_ask") is not None:
            up_prob = row.get("up_prob")
            if up_prob is not None and float(up_prob) >= 0.5:
                v = row.get("yes_ask")
    if v is None:
        return None
    v = float(v)
    return v if 0.0 < v < 1.0 else None


def _quote_stable_secs(ticks: list[dict], i: int, side: str) -> float:
    a0 = _exec_ask(ticks[i], side)
    t_i = _tick_epoch(ticks[i])
    if a0 is None or t_i is None:
        return 0.0
    stable_since = t_i
    for j in range(i - 1, -1, -1):
        aj = _exec_ask(ticks[j], side)
        t_j = _tick_epoch(ticks[j])
        if aj is None or t_j is None or abs(aj - a0) > 1e-4:
            break
        stable_since = t_j
    return t_i - stable_since


# Ticks are ~25s apart. A price seen at one tick and not the one before may
# be a quote that had just appeared and was gone before an order could land;
# the gate's pessimistic fill charges the worse of the two.
PRIOR_TICK_MAX_GAP_SECS = 60.0


def _prior_ask(ticks: list[dict], i: int, side: str) -> Optional[float]:
    """The side's ask at the tick before i, if it is recent enough to count."""
    if i <= 0:
        return None
    t_i, t_p = _tick_epoch(ticks[i]), _tick_epoch(ticks[i - 1])
    if t_i is None or t_p is None or not 0 <= t_i - t_p <= PRIOR_TICK_MAX_GAP_SECS:
        return None
    return _exec_ask(ticks[i - 1], side)


def _pessimistic_cost(ticks: list[dict], i: int, side: str, cost: float) -> float:
    prior = _prior_ask(ticks, i, side)
    return max(cost, prior) if prior is not None else cost


def _latency_fill(
    ticks: list[dict], i: int, side: str, a0: float,
    latency_secs: float, tol: float,
) -> Optional[tuple[float, int]]:
    t_i = _tick_epoch(ticks[i])
    if t_i is None:
        return None
    for j in range(i + 1, len(ticks)):
        t_j = _tick_epoch(ticks[j])
        if t_j is None or (t_j - t_i) < latency_secs:
            continue
        a1 = _exec_ask(ticks[j], side)
        if a1 is None or a1 > a0 + tol:
            return None
        return a1, j
    return None


def _missing_rule_fields(cfg: dict) -> list[str]:
    if not cfg.get("crypto15m_use_rules"):
        return []
    fields = {str(c.get("field")) for c in (cfg.get("crypto15m_rules") or [])}
    return sorted(fields - _DERIVABLE)


def _side_bid(row: dict, side: str) -> Optional[float]:
    if side == "up":
        v = row.get("yes_bid")
    else:
        ya = row.get("yes_ask")
        v = (1.0 - float(ya)) if ya is not None else None
    v = _num(v)
    return v if v is not None and 0.0 < v < 1.0 else None


def _exit_pnl_ct(
    ticks: list[dict], entry_idx: int, side: str, cost: float, cfg: dict
) -> Optional[tuple[float, str]]:
    """Per-contract P&L and reason of the first exit live trading would take.

    Mirrors crypto15m_trader._manage_position tick by tick. A sell-into-
    strength order rests from the first check after the fill and, while it
    rests, nothing else is checked. Otherwise take-profit (price target, then
    percent above cost, on the best bid) comes before the stop-loss (the side's
    mid below the exit threshold, or its percent loss past the stop), which
    sells at the bid, or two cents under the mid when there is no bid. None
    means the position is held to settlement.
    """
    at_entry = bt.tick_epoch(ticks[entry_idx])
    entry_fee = bt.us_fee_per_contract(cost, at_entry)

    def sell(price: float, row: dict, reason: str) -> tuple[float, str]:
        exit_fee = bt.us_fee_per_contract(price, bt.tick_epoch(row))
        return (price - cost) - entry_fee - exit_fee, reason

    held = {"status": "filled", "filled_contracts": 1, "cost_usd": cost,
            "avg_entry_cents": cost * 100.0}
    strength = crypto15m_trader.strength_exit_cents(held, cfg)
    stop_mid = crypto15m._const(cfg, "exit_threshold")
    stop_pct = crypto15m_trader._clamp01(crypto15m._const(cfg, "stop_loss_pct"))
    for row in ticks[entry_idx + 1:]:
        bid = _side_bid(row, side)
        bid_c = int(round(bid * 100)) if bid is not None else None
        if strength is not None:
            if bid_c is not None and bid_c >= strength:
                return sell(strength / 100.0, row, "sell_strength")
            continue
        if (crypto15m_trader.should_take_profit(held, bid_c, cfg)
                or crypto15m_trader.should_take_profit_pct(held, bid_c, cfg)):
            return sell(bid, row, "take_profit")
        up = _num(row.get("up_prob"))
        if up is None:
            continue
        mid = up if side == "up" else 1.0 - up
        if mid < stop_mid or (stop_pct > 0 and (cost - mid) / cost >= stop_pct):
            price = bid if bid is not None else max(0.01, mid - 0.02)
            return sell(price, row, "stop_loss")
    return None


def _tick_hour(row: dict) -> Optional[int]:
    ts = str(row.get("observed_at") or "")
    try:
        return int(ts[11:13])
    except (ValueError, IndexError):
        return None


def load_windows(env: str = "mainnet", interval: str = "15m",
                 since_days: int = 60) -> dict[str, list[dict]]:
    with dbmod.get_db() as conn:
        rows = conn.execute(
            """SELECT t.*, s.up_won, s.close_time AS sig_close
               FROM crypto15m_ticks t
               JOIN crypto15m_signals s
                 ON s.ticker = t.ticker AND s.network = t.network
               WHERE s.resolved = 1 AND s.up_won IS NOT NULL
                 AND t.network = ?
                 AND COALESCE(s.interval, '15m') = ?
                 AND t.observed_at >= datetime('now', ?)
               ORDER BY t.ticker, t.observed_at""",
            (env, interval, f"-{int(since_days)} days"),
        ).fetchall()
    by_window: dict[str, list[dict]] = {}
    for r in rows:
        by_window.setdefault(r["ticker"], []).append(dict(r))
    return by_window


def _simulate(
    by_window: dict[str, list[dict]], cfg: dict, *, contracts: int = 1,
    stable_secs: float = 0.0, latency_secs: float = 0.0, slip_tol: float = 0.0,
    pessimistic: bool = False,
) -> tuple[list[dict], int, int]:
    """Replay the entry rule over recorded windows.

    `pessimistic` is the evidence gate's fill: each leg pays the worse of its
    ask at the signal tick and at the tick before, and the entry is dropped
    at that tick (to be retried later, as live retries) when the price then
    breaks a cap live trading checks before it sends the order.
    """
    has_sched = isinstance(cfg.get("crypto15m_hour_configs"), dict)
    trades: list[dict] = []
    n_windows = 0
    latency_misses = 0
    for ticker, ticks in by_window.items():
        if not crypto15m.asset_enabled(cfg, str(ticks[0].get("asset") or "")):
            continue
        n_windows += 1
        up_won = int(ticks[0].get("up_won") or 0)
        close_iso = str(ticks[0].get("sig_close") or "")
        for i, t in enumerate(ticks):
            eff = cfg
            if has_sched:
                eff = crypto15m.hour_override(cfg, _tick_hour(t))
                if eff is None:
                    continue
            asset = tick_to_asset(t, eff, close_iso)
            try:
                ok, _why = crypto15m_trader.should_enter(
                    asset, eff, has_open=False, open_count=0,
                )
            except Exception:
                ok = False
            if not ok:
                continue
            if eff.get("crypto15m_paired_mode"):
                dom, dom_edge, _he = crypto15m_trader.paired_sides(asset)
                tilt = crypto15m_trader.paired_tilt(dom_edge, eff)
                if not dom or tilt < 1:
                    continue
                if stable_secs > 0 and (
                    _quote_stable_secs(ticks, i, "up") < stable_secs
                    or _quote_stable_secs(ticks, i, "down") < stable_secs
                ):
                    continue
                up_cost = float(asset["upAsk"])
                down_cost = float(asset["downAsk"])
                if pessimistic:
                    up_cost = _pessimistic_cost(ticks, i, "up", up_cost)
                    down_cost = _pessimistic_cost(ticks, i, "down", down_cost)
                    cap = float(eff.get("crypto15m_paired_max_combined_cents", 99.0) or 99.0)
                    if (up_cost + down_cost) * 100.0 > cap + 1e-9:
                        continue
                if latency_secs > 0:
                    f_up = _latency_fill(ticks, i, "up", up_cost, latency_secs, slip_tol)
                    f_down = _latency_fill(ticks, i, "down", down_cost, latency_secs, slip_tol)
                    if f_up is None or f_down is None:
                        latency_misses += 1
                        continue
                    up_cost, down_cost = f_up[0], f_down[0]
                dom_cost, hedge_cost = (
                    (up_cost, down_cost) if dom == "up" else (down_cost, up_cost)
                )
                dom_won = up_won if dom == "up" else (1 - up_won)
                _at = bt.tick_epoch(t)
                dom_fee = bt.us_fee_per_contract(dom_cost, _at)
                hedge_fee = bt.us_fee_per_contract(hedge_cost, _at)
                dom_pnl = (1.0 - dom_cost - dom_fee) if dom_won else (-dom_cost - dom_fee)
                hedge_pnl = (-hedge_cost - hedge_fee) if dom_won else (1.0 - hedge_cost - hedge_fee)
                pnl_unit = tilt * dom_pnl + hedge_pnl
                trades.append({
                    "ticker": ticker, "asset": asset["asset"], "side": dom,
                    "costCents": round((up_cost + down_cost) * 100, 1),
                    "minsLeft": asset["minsLeft"], "won": bool(dom_won),
                    "pnlUsd": round(pnl_unit * contracts, 4),
                    "at": t.get("observed_at"), "tilt": tilt,
                })
                break
            side = crypto15m_trader._entry_side(asset, eff)
            if side not in ("up", "down"):
                continue
            cost = asset["upAsk"] if side == "up" else asset["downAsk"]
            if not cost or not (0.0 < float(cost) < 1.0):
                continue
            cost = float(cost)
            if pessimistic:
                cost = _pessimistic_cost(ticks, i, side, cost)
                if cost > crypto15m._const(eff, "entry_max") + 1e-9:
                    continue
                if (eff.get("crypto15m_direction_mode") or "").lower() == "model":
                    ceiling = crypto15m_trader.model_price_ceiling_cents(asset, side, eff)
                    if ceiling is None or cost * 100.0 > ceiling + 1e-9:
                        continue
            if stable_secs > 0 and _quote_stable_secs(ticks, i, side) < stable_secs:
                continue
            fill_idx = i
            if latency_secs > 0:
                filled = _latency_fill(ticks, i, side, cost, latency_secs, slip_tol)
                if filled is None:
                    latency_misses += 1
                    continue
                cost, fill_idx = filled
            fee = bt.us_fee_per_contract(cost, bt.tick_epoch(t))
            won = up_won if side == "up" else (1 - up_won)
            pnl_ct = (1.0 - cost - fee) if won else (-cost - fee)
            exit_reason = "settlement"
            early = _exit_pnl_ct(ticks, fill_idx, side, cost, eff)
            if early is not None:
                pnl_ct, exit_reason = early
                won = pnl_ct > 0
            trades.append({
                "ticker": ticker, "asset": asset["asset"], "side": side,
                "costCents": round(cost * 100, 1),
                "minsLeft": asset["minsLeft"], "won": bool(won),
                "pnlUsd": round(pnl_ct * contracts, 4),
                "exitReason": exit_reason,
                "at": t.get("observed_at"),
            })
            break
    return trades, n_windows, latency_misses


def replay(cfg: dict, *, env: str = "mainnet", since_days: int = 60) -> dict:
    cfg = dict(cfg)
    cfg["crypto15m_enabled"] = True
    cfg["crypto15m_model_autopause"] = False
    contracts = max(1, int(cfg.get("crypto15m_order_size") or 1))
    stable_secs = float(cfg.get("replay_min_quote_stable_secs") or 0.0)
    latency_secs = float(cfg.get("replay_latency_secs") or 0.0)
    slip_tol = float(cfg.get("replay_latency_slip_tol_cents", 1.0) or 0.0) / 100.0
    latency_misses = 0
    interval = crypto15m._interval(cfg)
    by_window = load_windows(env=env, interval=interval, since_days=since_days)
    trades, n_windows, latency_misses = _simulate(
        by_window, cfg, contracts=contracts, stable_secs=stable_secs,
        latency_secs=latency_secs, slip_tol=slip_tol,
    )

    caveats = [
        f"Replayed the {interval} window series only (the interval this config trades); data recorded while the app watched other intervals is excluded.",
        "Entries fill at the recorded ask (taker, US fee θ·P·(1−P) at the dated schedule in force); real fills can be worse and marketable orders sometimes miss entirely.",
        "Only ticks with a REAL captured book are fillable — Gamma-fallback prices (CLOB WS cold) are treated as no quote, exactly like live.",
        "Top-of-book depth is not recorded — fills assume the full order size was available at the ask (fine at probe size, optimistic at scale).",
    ]
    _hcs = cfg.get("crypto15m_hour_configs")
    if isinstance(_hcs, dict) and _hcs:
        caveats.insert(0, (
            f"Per-hour schedule active: {len(_hcs)} of 24 UTC hours seated "
            "(each under its own generated config); unseated hours are "
            "untraded by design."
        ))
    elif isinstance(_hcs, dict):
        caveats.insert(0, (
            "Per-hour schedule active but EMPTY: 0 of 24 UTC hours seated, so "
            "this schedule trades nothing. Any zero result below is the empty "
            "schedule, not the strategy."
        ))
    if stable_secs > 0:
        caveats.append(
            f"Quote-stability filter ON: entries only where the side's ask sat "
            f"unchanged ≥{stable_secs:g}s across recorded ticks (a lower bound — "
            "ticks are ~4-25s apart, so genuinely-fresh fillable quotes are also "
            "excluded; treat as the conservative end)."
        )
    else:
        caveats.append(
            "Quote-stability filter OFF: entries can fill at asks that had JUST "
            "moved — on an independent 5m corpus that convention manufactured a "
            "large phantom edge from stale quotes (replay_min_quote_stable_secs "
            "to test)."
        )
    if latency_secs > 0:
        caveats.append(
            f"Signal-to-fill latency ON: fills at the first tick ≥{latency_secs:g}s "
            f"after the signal, missed when the ask ran >{slip_tol*100:.1f}¢ past "
            f"the signal price ({latency_misses} misses; the gate keeps retrying "
            "later ticks, matching the live loop)."
        )
    else:
        caveats.append(
            "Signal-to-fill latency OFF: fills at the signal tick itself; live "
            "submit-to-match is ~0.3-1.5s (replay_latency_secs to test)."
        )
    if cfg.get("crypto15m_paired_mode"):
        caveats.append(
            "Paired mode assumes BOTH legs fill at the recorded asks; live, a "
            "missed hedge leaves a naked single-side position."
        )
    caveats += [
        "Single-side exits ARE simulated in the live engine's order: a sell-into-strength order at its target, else take-profit (price or percent) at the recorded bid, else stop-loss (mid under the exit threshold, or percent loss) at the recorded bid, with entry and exit taker fees charged. Exits assume the sell fills at that price; live, a stop in a falling book can fill lower. Paired legs still hold to settlement.",
        f"Ticks are ~4-25s apart over {since_days} days of app uptime only; the gate could have fired between ticks.",
        "In-sample: any threshold tuned against this panel is fit to the past. Watch it run detection-only before arming.",
        "Live model-calibration auto-pause is NOT simulated — live trading can pause where this replay keeps trading.",
        "Final-minute sniper (model mode) needs a per-asset live WS spot. Ticks recorded before 2026-07-06 stored only the snapshot-wide feed tag, so their final-minute snipes may be over-admitted vs live; newer ticks record per-asset liveness.",
    ]
    missing = _missing_rule_fields(cfg)
    if missing:
        caveats.insert(0, (
            "Rules reference fields not recorded in ticks — those conditions "
            f"never match in replay (0 trades is expected): {', '.join(missing)}"
        ))
    if n_windows == 0:
        caveats.insert(0, (
            f"No resolved {interval} windows recorded in the last {since_days} "
            "days — the recorder only captures the interval selected on the "
            "Crypto tab, so switch to it there and let data accumulate first."
        ))
    # The verdict is the live gate's: its own fill model, one contract.
    gate_trades, _gw, _gm = _simulate(by_window, cfg, contracts=1, pessimistic=True)
    gate = gate_verdict(gate_trades, 1)
    caveats.append(
        "The live-gate verdict uses the gate's stricter fill: each entry pays the worse "
        "of the ask at its tick and the tick before (a quote that had only just "
        "appeared may be gone before an order lands), and is skipped at that tick when "
        "that price breaks the entry cap or the model's minimum edge.")
    out = _summarize(trades, contracts, n_windows, caveats)
    out["interval"] = interval
    out["gate"] = gate
    out["fillModel"] = {
        "minQuoteStableSecs": stable_secs,
        "latencySecs": latency_secs,
        "latencySlipTolCents": slip_tol * 100.0,
        "latencyMisses": latency_misses,
    }
    return out


def gate_verdict(trades: list[dict], contracts: int, *, now: Optional[datetime] = None) -> dict:
    """What the live evidence gate would say about these replayed trades.

    The gate looks only at the most recent EVIDENCE_DAYS, whatever window the
    backtest covers, and scores one contract per trade.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=crypto15m_evidence.EVIDENCE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    per_contract = max(1, int(contracts))
    recent = [dict(t, pnlUsd=float(t["pnlUsd"]) / per_contract)
              for t in trades if str(t.get("at") or "") >= cutoff]
    return crypto15m_evidence.assess(recent)


def _bucketize(trades: list[dict]) -> dict:
    by_hour = {h: {"n": 0, "wins": 0, "pnlUsd": 0.0} for h in range(24)}
    by_day: dict[str, dict] = {}
    for t in trades:
        ts = str(t.get("at") or "")
        try:
            hour = int(ts[11:13])
        except (ValueError, IndexError):
            hour = None
        day = ts[:10] if len(ts) >= 10 else None
        if hour is not None:
            b = by_hour[hour]
            b["n"] += 1
            b["wins"] += 1 if t["won"] else 0
            b["pnlUsd"] = round(b["pnlUsd"] + t["pnlUsd"], 4)
        if day:
            d = by_day.setdefault(day, {"n": 0, "wins": 0, "pnlUsd": 0.0})
            d["n"] += 1
            d["wins"] += 1 if t["won"] else 0
            d["pnlUsd"] = round(d["pnlUsd"] + t["pnlUsd"], 4)
    return {
        "byHourUtc": [{"hour": h, **by_hour[h]} for h in range(24)],
        "byDay": [{"day": d, **v} for d, v in sorted(by_day.items())],
    }


def _summarize(trades: list[dict], contracts: int, n_windows: int,
               caveats: list[str]) -> dict:
    trades = sorted(trades, key=lambda t: str(t.get("at") or ""))
    n = len(trades)
    wins = sum(1 for t in trades if t["won"])
    total = sum(t["pnlUsd"] for t in trades)
    denom = sum(int(t.get("contracts", contracts) or contracts) for t in trades)
    ev_ct = (total / denom * 100.0) if denom else 0.0
    by_asset: dict[str, dict] = {}
    for t in trades:
        a = by_asset.setdefault(t.get("asset") or "?", {"n": 0, "wins": 0, "pnlUsd": 0.0})
        a["n"] += 1
        a["wins"] += 1 if t["won"] else 0
        a["pnlUsd"] = round(a["pnlUsd"] + t["pnlUsd"], 4)
    equity, run = [], 0.0
    for t in trades:
        run += t["pnlUsd"]
        equity.append({"at": t.get("at"), "value": round(run, 4)})
    max_dd, peak = 0.0, 0.0
    for e in equity:
        peak = max(peak, e["value"])
        max_dd = min(max_dd, e["value"] - peak)
    if n < 30:
        caveats = [f"Only {n} trades — far too few for a verdict; treat as anecdote."] + list(caveats)
    t_stat = None
    if n >= 2:
        mean = total / n
        var = sum((t["pnlUsd"] - mean) ** 2 for t in trades) / (n - 1)
        if var > 1e-9:
            t_stat = round(max(-20.0, min(20.0, mean / (math.sqrt(var) / math.sqrt(n)))), 3)
    return {
        "n": n, "wins": wins,
        "winRate": round(wins / n, 4) if n else 0.0,
        "netEvCentsPerContract": round(ev_ct, 2),
        "totalPnlUsd": round(total, 2),
        "tStat": t_stat,
        "maxDrawdownUsd": round(max_dd, 2),
        "contracts": contracts,
        "windowsScanned": n_windows,
        "byAsset": by_asset,
        "equity": equity[-400:],
        "trades": trades[-50:],
        "caveats": caveats,
        **_bucketize(trades),
    }


def replay_main(cfg: dict, *, since_days: int = 60) -> dict:
    import main_recorder
    import portfolio_replay
    scenarios = {
        'base': dict(latency_ms=250, depth_fraction=1, slippage_cents=0),
        'delayed': dict(latency_ms=1000, depth_fraction=.5, slippage_cents=0),
        'stress': dict(latency_ms=2000, depth_fraction=.25, slippage_cents=1),
    }
    scenario = cfg.get('replay_scenario', 'base')
    if scenario not in scenarios:
        raise ValueError('Unknown replay scenario')
    return portfolio_replay.replay(cfg, main_recorder.load(since_days), **scenarios[scenario])
