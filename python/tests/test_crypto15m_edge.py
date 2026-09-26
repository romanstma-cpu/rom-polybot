"""The crypto engine trades only what its own recorded evidence supports.

Three layers, each tested here:
  * the model's tails: Student-t instead of a normal curve, so the model is
    not near-certain about moves that crypto makes routinely;
  * the calibration guard scores recent calls against what they cost, not
    only against a fixed hit rate;
  * live entries wait until replaying the configured strategy over recorded
    windows shows a profit that survives a conservative loss rate.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

import config
import crypto15m
import crypto15m_evidence as ev
import crypto15m_trader as c15t
import db
import replay


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "db_path", lambda: tmp_path / "edge.db")
    db.init_db()
    ev._verdicts.clear()
    ev._refreshing.clear()
    c15t._CAL_CACHE.update({"at": 0.0, "ok": True})
    yield
    ev._verdicts.clear()
    ev._refreshing.clear()
    c15t._CAL_CACHE.update({"at": 0.0, "ok": True})


# --- tails -------------------------------------------------------------------

@pytest.mark.parametrize("t,dof,cdf", [
    (2.5706, 5, 0.975), (4.0321, 5, 0.995), (6.8688, 5, 0.9995),
    (1.0, 1, 0.75), (2.0423, 30, 0.975), (-2.5706, 5, 0.025), (0.0, 5, 0.5),
])
def test_student_t_matches_published_quantiles(t, dof, cdf):
    assert crypto15m._student_t_cdf(t, dof) == pytest.approx(cdf, abs=2e-6)


def test_zero_tail_weight_is_the_old_normal_model():
    for z in (-3.0, -1.0, 0.0, 0.7, 2.5, 4.0):
        assert crypto15m._move_cdf(z, 0.0) == crypto15m._norm_cdf(z)
    # Many degrees of freedom converge on the normal curve too.
    assert crypto15m._move_cdf(2.0, 1e6) == pytest.approx(crypto15m._norm_cdf(2.0), abs=1e-6)


def test_fat_tails_doubt_the_far_tail_where_model_entries_live():
    """At equal variance a fat-tailed curve is more peaked in the middle and
    heavier in the tails. The two cross at about 97%, the model-mode entry
    threshold, so every model entry sees the more cautious of the two."""
    assert crypto15m._move_cdf(1.0, 5.0) > crypto15m._norm_cdf(1.0)
    crossing = next(z / 100 for z in range(100, 400)
                    if crypto15m._move_cdf(z / 100, 5.0) < crypto15m._norm_cdf(z / 100))
    assert 0.965 < crypto15m._norm_cdf(crossing) < 0.975
    normal, fat = crypto15m._norm_cdf(3.0), crypto15m._move_cdf(3.0, 5.0)
    assert normal > 0.998 and fat < 0.995
    # Three sigma out, the miss rate it expects is over four times the normal
    # curve's: at 99c that is the whole margin.
    assert (1 - fat) > 4 * (1 - normal)


def test_model_uses_fat_tails_by_default_and_stays_symmetric():
    spot, strike, sigma, mins = 100.3, 100.0, 0.001, 1.0   # three sigma above
    normal = crypto15m.model_up_prob(spot, strike, sigma, mins, tail_dof=0)
    default = crypto15m.model_up_prob(spot, strike, sigma, mins)
    assert default < normal
    for z in (0.5, 2.0, 3.5):
        assert crypto15m._move_cdf(z, 5.0) + crypto15m._move_cdf(-z, 5.0) == pytest.approx(1.0)


@pytest.mark.parametrize("raw,dof", [
    (None, 5.0), (0, 0.0), ("0", 0.0), (4, 4.0), (1, 2.5), ("junk", 5.0),
    (float("nan"), 5.0), (-3, 5.0),
])
def test_tail_setting_is_read_defensively(raw, dof):
    cfg = {} if raw is None else {"crypto15m_model_tail_dof": raw}
    assert crypto15m.model_tail_dof(cfg) == dof


@pytest.mark.parametrize("raw,stored", [(0, 0.0), (5, 5.0), (1, 2.5), (500, 100.0), ("x", 5.0)])
def test_config_validation_keeps_the_normal_option_and_clamps_the_rest(raw, stored):
    cfg = config.merge_with_defaults({"crypto15mModelTailDof": raw})
    assert cfg["crypto15m_model_tail_dof"] == stored


def test_replay_reprices_ticks_with_the_tail_setting_under_test():
    row = {"ticker": "T", "asset": "BTC", "mins_left": 1.0, "up_prob": 0.97,
           "yes_ask": 0.97, "up_ask": 0.97, "no_ask": 0.04,
           "spot": 100.3, "strike": 100.0, "sigma1m": 0.001,
           "model_prob": 0.5, "edge_net_cents": -40.0,
           "observed_at": "2026-08-01 12:00:00"}
    normal = replay.tick_to_asset(row, {"crypto15m_model_tail_dof": 0}, "")
    fat = replay.tick_to_asset(row, {"crypto15m_model_tail_dof": 5}, "")
    z = (100.3 - 100.0) / (0.001 * 1.0 * 100.3)
    assert normal["modelProb"] == pytest.approx(crypto15m._norm_cdf(z), abs=1e-12)
    assert fat["modelProb"] < normal["modelProb"]
    assert fat["edgeNetCents"] < normal["edgeNetCents"]


def test_replay_keeps_recorded_model_values_without_inputs():
    row = {"ticker": "T", "asset": "BTC", "mins_left": 1.0, "up_prob": 0.97,
           "model_prob": 0.991, "edge_net_cents": 1.5, "sigma1m": None,
           "spot": 100.3, "strike": 100.0, "observed_at": "2026-08-01 12:00:00"}
    asset = replay.tick_to_asset(row, {}, "")
    assert (asset["modelProb"], asset["edgeNetCents"]) == (0.991, 1.5)


# --- calibration guard against price ----------------------------------------

def _seed_calls(n_wins, n_losses, *, up_ask=None):
    with db.get_db() as conn:
        for i in range(n_wins + n_losses):
            tk = f"C{i}"
            conn.execute(
                """INSERT INTO crypto15m_signals
                   (ticker, asset, resolved, up_won, network, close_time)
                   VALUES (?, 'BTC', 1, ?, 'mainnet', '2026-01-01T00:00:00Z')""",
                (tk, 1 if i < n_wins else 0))
            conn.execute(
                """INSERT INTO crypto15m_ticks
                   (ticker, asset, mins_left, model_prob, up_ask, network)
                   VALUES (?, 'BTC', 2.0, 0.99, ?, 'mainnet')""",
                (tk, up_ask))


def test_a_good_hit_rate_still_pauses_when_it_loses_at_the_price(fresh_db):
    """38 of 40 is 95%, which the hit-rate floor accepts. At 98.5c a call,
    two misses cost more than 38 wins earn."""
    _seed_calls(38, 2, up_ask=0.985)
    cal = c15t.check_model_calibration("mainnet")
    assert cal["lb"] >= c15t._CAL_PAUSE_LB
    assert cal["ok"] is False
    assert cal["pricedN"] == 40 and cal["breakEven"] > 0.985
    assert "lost money at the prices on offer" in cal["reason"]


def test_the_same_record_is_fine_where_it_pays(fresh_db):
    _seed_calls(38, 2, up_ask=0.90)
    assert c15t.check_model_calibration("mainnet")["ok"] is True


def test_calls_without_a_recorded_ask_fall_back_to_the_hit_rate(fresh_db):
    _seed_calls(38, 2)
    cal = c15t.check_model_calibration("mainnet")
    assert cal["ok"] is True and cal["pricedN"] == 0 and cal["breakEven"] is None


def test_paused_guard_resumes_only_once_calls_pay_again(fresh_db):
    c15t._CAL_CACHE.update({"at": 0.0, "ok": False})
    _seed_calls(40, 0, up_ask=0.985)
    assert c15t.check_model_calibration("mainnet")["ok"] is True


def test_entry_reason_names_the_price_problem(fresh_db):
    _seed_calls(38, 2, up_ask=0.985)
    cfg = config.merge_with_defaults({
        "crypto15mEnabled": True, "crypto15mDirectionMode": "model",
        "crypto15mModelAutopause": True})
    asset = {"asset": "BTC", "hasMarket": True, "favorite": "up", "minsLeft": 5.0,
             "inWindow": True, "modelProb": 0.99, "upAsk": 0.90, "downAsk": 0.12,
             "spotLive": True}
    ok, why = c15t.should_enter(asset, cfg, has_open=False, open_count=0)
    assert not ok and "prices on offer" in why


# --- proven edge ------------------------------------------------------------

def _trades(results, *, cost=0.96, days=6, start=datetime(2026, 8, 1, tzinfo=timezone.utc)):
    """One contract per trade; `results` is a string of W/L in time order."""
    out = []
    fee = 0.0695 * cost * (1 - cost)
    for i, r in enumerate(results):
        at = start + timedelta(days=i * days / len(results))
        pnl = (1 - cost - fee) if r == "W" else -(cost + fee)
        out.append({"at": at.strftime("%Y-%m-%d %H:%M:%S"), "pnlUsd": pnl,
                    "costCents": cost * 100, "won": r == "W"})
    return out


def test_too_little_evidence_is_not_proof():
    v = ev.assess(_trades("W" * 30))
    assert not v["qualified"] and "not enough evidence" in v["reason"]
    v = ev.assess(_trades("W" * 80, days=2))
    assert not v["qualified"] and "2 of 5 days" in v["reason"]


def test_sixty_straight_wins_at_96c_prove_nothing():
    """No loss in 60 is consistent with a 4% loss rate, and at 96c a 4% loss
    rate loses money. A bootstrap alone would have called this proven."""
    v = ev.assess(_trades("W" * 60, cost=0.96))
    assert v["lowerEvCents"] > 0 and v["halvesPositive"]
    assert not v["qualified"] and "loss rate" in v["reason"]
    assert v["conservativeEvCents"] < 0


def test_a_real_edge_at_a_price_that_pays_qualifies():
    v = ev.assess(_trades(("W" * 11 + "L") * 6, cost=0.70))
    assert v["qualified"], v["reason"]
    assert v["conservativeEvCents"] > 0 and v["lowerEvCents"] > 0


def test_a_decline_in_the_later_half_blocks():
    v = ev.assess(_trades("W" * 40 + ("WWL" * 10), cost=0.70))
    assert v["evCents"] > 0
    assert not v["qualified"] and "later half" in v["reason"]


def test_slippage_is_charged_on_every_trade():
    v = ev.assess(_trades("W" * 60, cost=0.50))
    fee = 0.0695 * 0.25
    assert v["evCents"] == pytest.approx(100 * (0.5 - fee - ev.SLIPPAGE_USD), abs=0.01)


def test_sizing_and_limits_do_not_invalidate_a_verdict():
    base = config.merge_with_defaults({})
    same = dict(base, crypto15m_order_size=50, crypto15m_daily_loss_limit=-5.0,
                crypto15m_enabled=not base["crypto15m_enabled"])
    other = dict(base, crypto15m_entry_threshold=0.80)
    assert ev.fingerprint(same) == ev.fingerprint(base)
    assert ev.fingerprint(other) != ev.fingerprint(base)


def test_gate_waits_for_a_verdict_and_follows_it(fresh_db):
    cfg = config.merge_with_defaults({})
    ok, why = ev.gate(cfg, "mainnet")
    assert not ok and "checking" in why
    ev._verdicts[("mainnet", ev.fingerprint(cfg))] = {
        "qualified": False, "reason": "not proven: x", "evaluatedAt": 0}
    ok, why = ev.gate(cfg, "mainnet")
    assert not ok and "not proven: x" in why
    ev._verdicts[("mainnet", ev.fingerprint(cfg))]["qualified"] = True
    assert ev.gate(cfg, "mainnet") == (True, "")
    assert ev.gate(dict(cfg, crypto15m_require_proven_edge=False), "mainnet") == (True, "")


def _seed_replay_window(conn, ticker, day, up_won, ask):
    db.insert_crypto15m_signal(conn, {
        "ticker": ticker, "asset": "BTC", "series": "BTC-UPDOWN",
        "close_time": f"{day}T00:15:00Z", "mins_left": 1.5,
        "favorite": "up", "favorite_price": ask, "entry_cost": ask,
        "up_prob": ask, "delta_pct": 0.002, "interval": "15m",
    })
    db.resolve_crypto15m_signal(conn, ticker, up_won)
    db.insert_crypto15m_tick(conn, {
        "ticker": ticker, "asset": "BTC", "mins_left": 1.5,
        "yes_bid": ask - 0.02, "yes_ask": ask, "up_prob": ask,
        "spot": 100.0, "open_spot": 99.8, "delta_pct": 0.002,
        "ws_bid": ask - 0.02, "ws_ask": ask, "up_ask": ask,
        "no_ask": round(1.0 - ask + 0.02, 4), "spot_source": "rtds-ws",
        "interval": "15m",
    })
    conn.execute("UPDATE crypto15m_ticks SET observed_at=? WHERE ticker=?",
                 (f"{day} 00:13:30", ticker))


def _favorite_cfg():
    cfg = config.merge_with_defaults({})
    cfg.update({"crypto15m_direction_mode": "favorite", "crypto15m_use_rules": False,
                "crypto15m_paired_mode": False, "crypto15m_time_delay_min": 2.0,
                "crypto15m_entry_threshold": 0.90, "crypto15m_entry_max": 0.99,
                "crypto15m_min_delta_pct": 0.0005})
    return cfg


def test_evaluate_replays_the_engines_own_rule(fresh_db):
    today = datetime.now(timezone.utc).date()
    with db.get_db() as conn:
        for i in range(60):
            day = (today - timedelta(days=1 + i % 6)).isoformat()
            _seed_replay_window(conn, f"0x{i:03d}", day, up_won=1, ask=0.95)
    v = ev.evaluate(_favorite_cfg(), "mainnet")
    assert v["n"] == 60 and v["days"] == 6 and v["windows"] == 60
    # 60 of 60 at 95c: a real sample, and still not proof.
    assert not v["qualified"] and "loss rate" in v["reason"]


def test_run_loop_verdict_is_cached_and_exposed(fresh_db):
    cfg = _favorite_cfg()

    async def go():
        ev.ensure_fresh(cfg, "mainnet")
        task = ev._refreshing[("mainnet", ev.fingerprint(cfg))]
        await task
        ev.ensure_fresh(cfg, "mainnet")   # fresh: no second replay
        return task is ev._refreshing[("mainnet", ev.fingerprint(cfg))]

    assert asyncio.run(go())
    v = ev.verdict(cfg, "mainnet")
    assert v is not None and v["qualified"] is False and v["n"] == 0
    assert "not enough evidence" in v["reason"]


def test_a_failed_replay_blocks_and_retries_early(fresh_db, monkeypatch):
    cfg = _favorite_cfg()

    def boom(*_a, **_kw):
        raise RuntimeError("disk")
    monkeypatch.setattr(ev, "evaluate", boom)

    async def go():
        ev.ensure_fresh(cfg, "mainnet")
        await ev._refreshing[("mainnet", ev.fingerprint(cfg))]

    asyncio.run(go())
    v = ev.verdict(cfg, "mainnet")
    assert v["qualified"] is False and "replay failed" in v["reason"]
    age_until_stale = ev.REFRESH_SEC - (time.time() - v["evaluatedAt"])
    assert age_until_stale <= ev.RETRY_FAILED_SEC + 1
    assert not ev.gate(cfg, "mainnet")[0]


def test_status_starts_the_replay_before_the_engine_is_on(fresh_db, monkeypatch):
    started = []
    monkeypatch.setattr(ev, "ensure_fresh", lambda cfg, env: started.append(env))
    cfg = dict(_favorite_cfg(), crypto15m_enabled=False)
    assert c15t._evidence_for_status(cfg, "mainnet") is None
    assert started == ["mainnet"]
    started.clear()
    c15t._evidence_for_status(dict(cfg, crypto15m_require_proven_edge=False), "mainnet")
    assert started == []
