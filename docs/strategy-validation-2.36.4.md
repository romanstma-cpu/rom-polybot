# Entry selection and fresh-flow scheduling

## Diagnosis and changes

The default Momentum scan interval was 90 seconds while a usable window must
contain a trade from the previous 30 seconds. A burst just after a scheduled scan
could disappear before the next scan. New validated, deduplicated US exchange
receipts now trigger Large Trade and Momentum scans with a 10-second debounce.
Configured intervals remain periodic fallbacks. This is scheduling, not a
guaranteed execution-latency SLA: slow tasks and reconnects can still delay work.

Live candidate retrieval also applied the heuristic minimum confidence before
consulting calibration. That score is market price plus a bounded flow score,
not a measured win probability. Qualified live selection now retrieves fresh
candidates without that score cutoff, then checks conservative calibrated edge
at the signal price. Direction conflicts use the same qualified selection gate.
Execution independently rechecks edge at the current order price, plus fees,
spread, slippage, visible depth, balance, minimum quantity and portfolio limits.
Practice retains its configured heuristic filters; custom entry rules remain
explicit user filters. No live qualification thresholds were relaxed.

## Exact parameters

- Fresh-flow debounce: `signal_schedule.FRESH_FLOW_SCAN_SECONDS = 10.0`.
- Periodic fallback defaults: `whale_scan_interval = 120`,
  `momentum_scan_interval = 90`; trade scan stays at 5 seconds.
- Live gate: `require_qualified_edge = true` (unchanged).
- Net edge defaults: `min_edge_pts_whale = 5`,
  `min_edge_pts_momentum = 5` (unchanged).
- No changes to bankroll, fees, risk sizing, exposure, drawdown or order style.

## Regression validation

Tests reproduce a warmed tape receiving a burst immediately after its prior
scan: the receipt-triggered scan sees a fresh usable window at +10 seconds;
the old periodic scan finds it stale at +90 seconds. Repeated/processed receipts
do not cause scan floods. Periodic and shorter user intervals still work.

Synthetic model tests verify low-score candidates with qualified net edge
reach the live execution boundary and replay. Future, stale, missing and
low-edge evidence remain blocked; category, signal-type and price filters
still apply. Synthetic models establish code behavior, not returns.

## Paper and backtest acceptance plan

1. Run Practice and recording with the account unfunded. Confirm watched
   markets receive valid US trades, warm-up completes, signals are recorded,
   and the decision history explains every blocked candidate. No live order
   or funded account is needed to collect observations.
2. Compare old and new schedules on the same timestamped receipt log. Count
   fresh windows captured, receipt-to-signal latency, duplicate signals, feed
   gaps and scan duration. A signal-only replay cannot measure missed bursts;
   this comparison requires raw receipt timestamps.
3. Replay old and new selection on identical recorded signals, books and
   settlement events with identical bankroll and risk settings. Train each
   model only on outcomes known at that simulated time. Retain the chronological
   holdout, independent-event counting, one-day embargo, minimum sample counts
   and fee/price stress tests. Compare net return on risk, drawdown, fill rate,
   turnover, rejected entries and event concentration.
4. Keep signals without qualified evidence in observation/Practice. Faster
   scanning is not permission to deploy an unvalidated flow strategy. Observe
   fresh forward predictions and settlements separately from fitting data.
5. Only after the existing evidence and risk gates pass, fund a limited live
   trial and compare actual fills, fees, adverse-selection markouts and net
   results against Practice. Stop on risk limits or execution faults.

Neither this change nor simulated passing tests demonstrate profitability.
Zero buying power correctly prevents live orders; no change forces trades.
