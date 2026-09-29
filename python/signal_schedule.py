"""Debounce fresh exchange receipts without waiting out a stale scan timer."""

FRESH_FLOW_SCAN_SECONDS = 10.0


def scan_due(now: float, last_scan: float, interval: float,
             revision: int, last_revision: int) -> bool:
    elapsed = now - last_scan
    return (elapsed >= interval or
            (revision > last_revision and
             elapsed >= min(interval, FRESH_FLOW_SCAN_SECONDS)))
