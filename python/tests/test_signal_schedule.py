from signal_schedule import scan_due
from momentum_window import Tape
from test_momentum_window import T, trade


def test_new_burst_is_scanned_before_thirty_second_freshness_expires():
    assert not scan_due(109, 100, 90, 1, 0)
    assert scan_due(110, 100, 90, 1, 0)
    assert 110 - 101 < 30  # a receipt at 101 remains fresh


def test_processed_receipts_do_not_cause_repeated_scans():
    assert not scan_due(150, 110, 90, 1, 1)
    assert scan_due(200, 110, 90, 1, 1)


def test_continuous_receipts_are_debounced():
    for elapsed in range(1, 10):
        assert not scan_due(100 + elapsed, 100, 90, elapsed, 0)
    assert scan_due(110, 100, 90, 10, 0)


def test_short_user_interval_and_periodic_fallback_are_preserved():
    assert scan_due(105, 100, 5, 0, 0)
    assert scan_due(190, 100, 90, 0, 0)
    assert not scan_due(109, 100, 90, 0, 0)


def test_receipts_arriving_during_scan_are_not_marked_processed():
    # The caller saves the revision captured before its awaited scan.
    assert scan_due(120, 110, 90, 2, 1)


def test_receipt_schedule_catches_a_real_window_that_periodic_scan_misses():
    tape = Tape()
    assert tape.add(trade('warmup'), T)
    last_scan = T + 300
    last_revision = tape.accepted
    assert not tape.summarize('M1', last_scan)['ready']
    for i in range(5):
        assert tape.add(trade(f'burst-{i}', at=last_scan+1, qty=1000), last_scan+1)
    scan_at = last_scan+10
    assert scan_due(scan_at, last_scan, 90, tape.accepted, last_revision)
    assert tape.summarize('M1', scan_at)['ready']
    assert not tape.summarize('M1', last_scan+90)['ready']
